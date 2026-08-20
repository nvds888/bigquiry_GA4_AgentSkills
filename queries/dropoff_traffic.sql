WITH windowed AS (
  SELECT *
  FROM `bigquery-public-data.thelook_ecommerce.events`
  WHERE DATE(created_at) BETWEEN DATE '2026-02-17' AND DATE '2026-08-16'
),
per_session AS (
  SELECT
    session_id,
    ANY_VALUE(traffic_source) AS segment,
    -- [LOOP_START]
    MAX(IF(event_type = '{step}', 1, 0)) AS s_{step}
    -- [LOOP_END]
  FROM windowed
  GROUP BY session_id
),
by_segment AS (
  SELECT
    segment,
    COUNT(*) AS sessions,
    -- [LOOP_START]
    SUM(s_{step}) AS sum_{step}
    -- [LOOP_END]
  FROM per_session
  GROUP BY segment
)
SELECT
  segment,
  sessions,
  -- [LOOP_START]
  ROUND(100 * SAFE_DIVIDE(sum_{next_step}, sum_{step}), 2) AS {step}_to_{next_step}_conv_pct,
  ROUND(100 * (1 - SAFE_DIVIDE(sum_{next_step}, sum_{step})), 2) AS {step}_to_{next_step}_drop_pct
  -- [LOOP_END]
FROM by_segment
ORDER BY sessions DESC
