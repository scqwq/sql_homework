SELECT u.DisplayName
FROM users u
INNER JOIN posts p ON u.Id = p.OwnerUserId
WHERE u.DisplayName IN ('Harvey Motulsky', 'Noah Snyder')
GROUP BY u.DisplayName
ORDER BY SUM(p.Score) DESC
LIMIT 1;