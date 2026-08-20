WITH windowed AS (
  SELECT user_id, traffic_source, event_type
  FROM `bigquery-public-data.thelook_ecommerce.events`
  WHERE DATE(created_at) BETWEEN DATE '2026-02-17' AND DATE '2026-08-16'
)
SELECT
  traffic_source AS segment,
  COUNT(DISTINCT user_id) AS users,
  COUNT(DISTINCT IF(event_type = '{final_step}', user_id, NULL)) AS converters,
  SAFE_DIVIDE(
    COUNT(DISTINCT IF(event_type = '{final_step}', user_id, NULL)),
    COUNT(DISTINCT user_id)
  ) AS conv_rate
FROM windowed
GROUP BY segment
ORDER BY users DESC
