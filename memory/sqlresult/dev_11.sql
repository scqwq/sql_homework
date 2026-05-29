SELECT COALESCE(SUM(v.BountyAmount), 0) AS total_bounty
FROM posts p
JOIN votes v ON p.Id = v.PostId
WHERE p.Title LIKE '%data%';