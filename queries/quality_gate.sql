WITH windowed AS (
  SELECT *
  FROM `bigquery-public-data.thelook_ecommerce.events`
  WHERE DATE(created_at) BETWEEN DATE '2026-02-17' AND DATE '2026-08-16'
)
SELECT
  COUNT(*) AS total_events,
  COUNTIF(id IS NULL) AS null_id,
  COUNTIF(session_id IS NULL OR session_id = '') AS null_session,
  COUNTIF(user_id IS NULL) AS null_user,
  COUNT(DISTINCT id) AS distinct_ids,
  SAFE_DIVIDE(COUNT(*) - COUNT(DISTINCT id), COUNT(*)) AS dup_id_rate
FROM windowed
