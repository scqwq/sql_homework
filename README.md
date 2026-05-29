为减少体积，对应数据库文件不上传，放在同目录下即可

dev_data训练集

test_data测试集



memory/

chathistory：LLM memory简易存放，直接用txt了懒得接sqlite了（

sqlresult：LLM返回的对应问题的查询语句

result：数据库查询返回的json格式的文件





LLMJson:

大模型不知道表相关数据则无法根据需求和表名、列名生成查询语句，

但是若是把全部表输入给大模型会使用大量token和上下文，

所以采取将其中的表名和列名提取，并修改prompt，让LLM直到表的相关信息

表明、列名等关系被存放于LLMJSON中



训练集正确率75%~83.3%，使用DeepSeek V4 flash可达到75%