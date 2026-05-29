SELECT DisplayName
FROM users
WHERE CAST(strftime('%Y', CreationDate) AS INTEGER) = 2011;