WITH windowed AS (
  SELECT *
  FROM `bigquery-public-data.thelook_ecommerce.events`
  WHERE DATE(created_at) BETWEEN DATE '2026-02-17' AND DATE '2026-08-16'
),
per_session AS (
  SELECT
    session_id,
    ANY_VALUE(traffic_source) AS traffic_source,
    MAX(IF(event_type = 'product', 1, 0)) AS s_product,
    MAX(IF(event_type = 'cart', 1, 0)) AS s_cart,
    MAX(IF(event_type = 'purchase', 1, 0)) AS s_purchase,
    MAX(IF(event_type = 'cancel', 1, 0)) AS s_cancel
  FROM windowed
  GROUP BY session_id
)
SELECT
  traffic_source AS segment,
  COUNT(*) AS sessions,
  SAFE_DIVIDE(SUM(s_product), COUNT(*)) AS product_view_rate,
  SAFE_DIVIDE(SUM(s_cart), COUNT(*)) AS cart_rate,
  SAFE_DIVIDE(SUM(s_purchase), COUNT(*)) AS purchase_rate,
  SAFE_DIVIDE(SUM(s_cancel), COUNT(*)) AS cancel_rate,
  SAFE_DIVIDE(SUM(s_purchase), SUM(s_cart)) AS cart_to_purchase_rate
FROM per_session
GROUP BY segment
ORDER BY sessions DESC
