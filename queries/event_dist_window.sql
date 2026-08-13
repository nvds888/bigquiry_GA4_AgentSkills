SELECT event_type, COUNT(*) AS n, ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER(), 1) AS pct
FROM `bigquery-public-data.thelook_ecommerce.events`
WHERE DATE(created_at) BETWEEN DATE '2026-02-17' AND DATE '2026-08-16'
GROUP BY event_type
ORDER BY n DESC
