SELECT p.FavoriteCount
FROM comments c
JOIN posts p ON c.PostId = p.Id
WHERE c.UserId = 3025 AND c.CreationDate = '2014/4/23 20:29:39.0';