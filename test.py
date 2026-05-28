import sys
import json
import requests
import sqlite3
from pathlib import Path
from typing import Dict, List

# 禁用 SSL 警告
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 强制 UTF-8 编码
sys.stdout.reconfigure(encoding="utf-8")

# 读取 .env 文件
_env_path = Path(__file__).parent / ".env"

def _read_env_file(path: Path) -> Dict[str, str]:
    for enc in ("utf-8", "gbk"):
        try:
            text = path.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    else:
        text = path.read_text(encoding="utf-8", errors="replace")

    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip().strip("\"'")
    return result

_raw_env = _read_env_file(_env_path) if _env_path.exists() else {}

# ── 配置 ──────────────────────────────────────────────
# TODO: 在此修改自定义提示词
CUSTOM_PROMPT = "以下是一些示例，展示了如何将自然语言查询转换为 SQL 语句。请根据这些示例来理解用户的查询需求，并在读取的json文件没有SQL示例的测试集中生成相应的 SQL 语句。"

DB_PATH = _raw_env.get("DB_PATH") or str(Path(__file__).parent / "database.db")
API_KEY = _raw_env.get("API_KEY", "")
LLM_MODEL = _raw_env.get("LLM_MODEL", "deepseek-chat")
LLM_URL = _raw_env.get("LLM_URL", "https://api.deepseek.com").rstrip("/")

MEMORY_DIR = Path(__file__).parent / "memory"
MEMORY_FILE = MEMORY_DIR / "chat_history.txt"
SQLRESULT_DIR = MEMORY_DIR / "sqlresult"
RESULT_DIR = MEMORY_DIR / "result"
LLMJSON_DIR = Path(__file__).parent / "LLMjson"
DEV_DATA_FILE = Path(__file__).parent / "dev_data.json"
TEST_DATA_FILE = Path(__file__).parent / "test_data.json"


# ── SQLite 数据库 ──────────────────────────────────────
def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_schema() -> str:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cur.fetchall()]

    schema_parts = []
    for table in tables:
        cur.execute(f"PRAGMA table_info({table})")
        columns = cur.fetchall()
        col_desc = ", ".join(
            f"{col['name']} ({col['type']}{'' if col['notnull'] else ' NULL'})"
            for col in columns
        )
        schema_parts.append(f"表 {table}: {col_desc}")

    cur.close()
    conn.close()
    return "\n".join(schema_parts)


def get_schema_metadata() -> Dict:
    """获取数据库元数据，返回 JSON 格式"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cur.fetchall()]

    metadata = {"database": DB_PATH, "tables": {}}

    for table in tables:
        cur.execute(f"PRAGMA table_info({table})")
        columns = cur.fetchall()
        metadata["tables"][table] = {
            "columns": [
                {
                    "name": col["name"],
                    "type": col["type"],
                    "not_null": bool(col["notnull"]),
                    "primary_key": bool(col["pk"]),
                }
                for col in columns
            ]
        }

    cur.close()
    conn.close()
    return metadata


def save_schema_metadata() -> Path:
    """保存数据库元数据到 LLMjson/db_metadata.json"""
    LLMJSON_DIR.mkdir(parents=True, exist_ok=True)
    metadata = get_schema_metadata()
    file_path = LLMJSON_DIR / "db_metadata.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    print(f"数据库元数据已保存到: {file_path}")
    return file_path


# ── 数据加载 ──────────────────────────────────────────
def load_json(file_path: Path) -> List[Dict]:
    if not file_path.exists():
        print(f"未找到 {file_path}")
        return []
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_few_shot_examples(dev_data: List[Dict]) -> str:
    lines = ["以下是自然语言转SQL的示例："]
    for item in dev_data:
        lines.append(f"问题: {item.get('question', '')}")
        lines.append(f"SQL: {item.get('SQL', '')}")
        lines.append("")
    return "\n".join(lines)


# ── 大模型调用 ────────────────────────────────────────
def llm_chat(messages: List[Dict]) -> str:
    headers = {"Content-Type": "application/json"}
    if API_KEY and API_KEY != "no-key":
        headers["Authorization"] = f"Bearer {API_KEY}"

    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "max_tokens": 2048,
        "temperature": 0.1,
    }

    url = LLM_URL
    if not url.endswith("/chat/completions"):
        url = url.rstrip("/") + "/v1/chat/completions"

    resp = requests.post(url, json=payload, headers=headers, timeout=120, verify=False)
    resp.raise_for_status()
    data = resp.json()

    if "choices" in data:
        message = data["choices"][0]["message"]
        content = message.get("content", "").strip()

        # 如果 content 为空，尝试从 reasoning_content 中提取 SQL
        if not content and "reasoning_content" in message:
            reasoning = message["reasoning_content"]
            # 从推理内容中提取最后的 SQL 语句
            import re
            sql_match = re.search(r'(?:SQL|sql)[：:]\s*(SELECT.+?)(?:\n|$)', reasoning, re.DOTALL)
            if sql_match:
                content = sql_match.group(1).strip()
            else:
                # 尝试找最后一个 SELECT 语句
                sql_matches = re.findall(r'(SELECT\s+.+?;)', reasoning, re.DOTALL | re.IGNORECASE)
                if sql_matches:
                    content = sql_matches[-1].strip()

        # 去除 markdown 代码块标记
        if content.startswith("```"):
            lines = content.split("\n")
            # 移除第一行（```sql 或 ```）
            lines = lines[1:]
            # 移除最后一行（```）
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        # 确保 SQL 以分号结尾
        if content and not content.endswith(";"):
            content += ";"

        return content
    raise ValueError(f"无法解析 LLM 返回: {json.dumps(data, ensure_ascii=False)[:300]}")


def text_to_sql(question: str, metadata_file: Path, few_shot: str) -> str:
    # 加载元数据 JSON
    with open(metadata_file, "r", encoding="utf-8") as f:
        metadata = json.load(f)

    # 构建表结构描述
    table_descs = []
    for table_name, table_info in metadata["tables"].items():
        cols = []
        for col in table_info["columns"]:
            col_str = f"{col['name']} ({col['type']}"
            if col["primary_key"]:
                col_str += ", PRIMARY KEY"
            elif col["not_null"]:
                col_str += ", NOT NULL"
            col_str += ")"
            cols.append(col_str)
        cols_str = ", ".join(cols)
        table_descs.append(f"表 {table_name}: {cols_str}")

    schema_text = "\n".join(table_descs)

    system_prompt = f"""你是一个 SQLite 专家。根据以下数据库 schema 和示例，将用户的自然语言查询转换为准确的 SQL 语句。

数据库 Schema:
{schema_text}

{few_shot}

规则:
1. 只输出 SQL 语句，不要包含任何解释、注释或 markdown 代码块标记
2. 使用标准的 SQLite 语法
3. 如果查询涉及多表连接，请使用明确的 JOIN 语法
4. 适当使用 WHERE、GROUP BY、ORDER BY 等子句
5. 注意字段名的大小写和命名约定"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]

    return llm_chat(messages)


# ── SQL 执行 ──────────────────────────────────────────
def execute_sql(sql: str) -> List[Dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(row) for row in rows]


# ── 文件保存 ──────────────────────────────────────────
def save_to_file(file_path: Path, content: str):
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)


# ── 主流程 ────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("SQL 批量生成工具 (SQLite + LLM)")
    print("=" * 60)

    # 加载数据
    dev_data = load_json(DEV_DATA_FILE)
    test_data = load_json(TEST_DATA_FILE)

    if not dev_data:
        print("dev_data.json 为空，无法生成示例")
        sys.exit(1)

    if not test_data:
        print("test_data.json 为空，无测试数据")
        sys.exit(1)

    print(f"已加载 {len(dev_data)} 条示例数据")
    print(f"已加载 {len(test_data)} 条测试数据")

    # 获取数据库 Schema 并保存为 JSON
    try:
        metadata_file = save_schema_metadata()
        print(f"\n数据库 Schema:")
        metadata = get_schema_metadata()
        for table_name, table_info in metadata["tables"].items():
            cols = [col["name"] for col in table_info["columns"]]
            print(f"  {table_name}: {', '.join(cols)}")
    except Exception as e:
        print(f"获取 Schema 失败: {e}")
        sys.exit(1)

    # 构建 few-shot 示例
    few_shot = build_few_shot_examples(dev_data)

    # 创建输出目录
    SQLRESULT_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    # 批量处理
    print(f"\n{'='*60}")
    print("开始批量处理...")
    print(f"{'='*60}")

    for item in test_data:
        qid = item.get("question_id", "unknown")
        question = item.get("question", "")
        print(f"\n[{qid}] {question}")

        try:
            sql = text_to_sql(question, metadata_file, few_shot)
        except Exception as e:
            print(f"  LLM 调用失败: {e}")
            sql = f"-- LLM 调用失败: {e}"

        print(f"  SQL: {sql[:100]}...")

        # 保存 SQL
        sql_file = SQLRESULT_DIR / f"{qid}.sql"
        save_to_file(sql_file, sql)

        # 执行 SQL
        if not sql.startswith("--"):
            try:
                rows = execute_sql(sql)
                result_content = json.dumps(rows, ensure_ascii=False, indent=2, default=str)
                print(f"  结果: {len(rows)} 行")
            except Exception as e:
                result_content = f"-- 执行失败: {e}"
                print(f"  执行出错: {e}")
        else:
            result_content = sql

        # 保存结果
        result_file = RESULT_DIR / f"{qid}.json"
        save_to_file(result_file, result_content)

    print(f"\n{'='*60}")
    print("处理完成！")
    print(f"SQL 保存在: {SQLRESULT_DIR}")
    print(f"结果保存在: {RESULT_DIR}")
    print(f"{'='*60}")
