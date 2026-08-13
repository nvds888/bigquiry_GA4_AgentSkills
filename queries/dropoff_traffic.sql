WITH windowed AS (
  SELECT *
  FROM `bigquery-public-data.thelook_ecommerce.events`
  WHERE DATE(created_at) BETWEEN DATE '2026-02-17' AND DATE '2026-08-16'
),
per_session AS (
  SELECT
    session_id,
    ANY_VALUE(traffic_source) AS segment,
    MAX(IF(event_type = 'product', 1, 0)) AS s_product,
    MAX(IF(event_type = 'cart', 1, 0)) AS s_cart,
    MAX(IF(event_type = 'purchase', 1, 0)) AS s_purchase
  FROM windowed
  GROUP BY session_id
),
baseline AS (
  SELECT
    SAFE_DIVIDE(SUM(s_product), COUNT(*)) AS product_view_rate,
    SAFE_DIVIDE(SUM(s_cart), COUNT(*)) AS cart_rate,
    SAFE_DIVIDE(SUM(s_purchase), COUNT(*)) AS purchase_rate,
    SAFE_DIVIDE(SUM(s_purchase), SUM(s_cart)) AS cart_to_purchase_rate
  FROM per_session
),
by_segment AS (
  SELECT
    segment,
    COUNT(*) AS sessions,
    SAFE_DIVIDE(SUM(s_product), COUNT(*)) AS product_view_rate,
    SAFE_DIVIDE(SUM(s_cart), COUNT(*)) AS cart_rate,
    SAFE_DIVIDE(SUM(s_purchase), COUNT(*)) AS purchase_rate,
    SAFE_DIVIDE(SUM(s_purchase), SUM(s_cart)) AS cart_to_purchase_rate,
    SAFE_DIVIDE(SUM(s_cancel), COUNT(*)) AS cancel_rate
  FROM (
    SELECT session_id, ANY_VALUE(traffic_source) AS segment,
      MAX(IF(event_type='product',1,0)) s_product,
      MAX(IF(event_type='cart',1,0)) s_cart,
      MAX(IF(event_type='purchase',1,0)) s_purchase,
      MAX(IF(event_type='cancel',1,0)) s_cancel
    FROM windowed GROUP BY session_id
  )
  GROUP BY segment
)
SELECT
  b.segment,
  b.sessions,
  ROUND(100 * b.purchase_rate, 2) AS purchase_pct,
  ROUND(100 * (b.purchase_rate - bl.purchase_rate), 2) AS purchase_vs_baseline_pp,
  ROUND(100 * b.cart_to_purchase_rate, 2) AS cart_to_purchase_pct,
  ROUND(100 * (b.cart_to_purchase_rate - bl.cart_to_purchase_rate), 2) AS cart_conv_vs_baseline_pp,
  ROUND(100 * (1 - SAFE_DIVIDE(b.cart_rate, b.product_view_rate)), 2) AS product_to_cart_drop_pct,
  ROUND(100 * (1 - b.cart_to_purchase_rate), 2) AS cart_to_purchase_drop_pct
FROM by_segment b
CROSS JOIN baseline bl
ORDER BY b.sessions DESC
