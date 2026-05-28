SELECT 
    (SELECT GROUP_CONCAT(DISTINCT PostHistoryTypeId) FROM postHistory WHERE PostId = 3720) AS post_history_type_ids,
    (SELECT COUNT(DISTINCT UserId) FROM comments WHERE PostId = 3720) AS unique_commenters;