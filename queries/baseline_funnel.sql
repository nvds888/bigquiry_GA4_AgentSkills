WITH windowed AS (
  SELECT *
  FROM `bigquery-public-data.thelook_ecommerce.events`
  WHERE DATE(created_at) BETWEEN DATE '2026-02-17' AND DATE '2026-08-16'
),
per_session AS (
  SELECT
    session_id,
    -- [LOOP_START]
    MAX(IF(event_type = '{step}', 1, 0)) AS s_{step}
    -- [LOOP_END]
  FROM windowed
  GROUP BY session_id
)
SELECT
  COUNT(*) AS total_sessions,
  -- [LOOP_START]
  SAFE_DIVIDE(SUM(s_{step}), COUNT(*)) AS {step}_rate
  -- [LOOP_END]
FROM per_session
