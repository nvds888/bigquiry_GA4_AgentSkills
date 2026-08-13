# Shared SQL templates & thresholds (GA4)

Single source of truth for the SQL and thresholds used by `ga4-events` and
`ga4-tracking-audit`. Both skills point here so checks stay in sync — edit
templates in this file, never inline in a SKILL.md.

Substitute `{project_id}`, `{dataset}`, `{start}`, `{end}` per run. GA4
exports are date-partitioned: always filter `_TABLE_SUFFIX BETWEEN ...`,
dry-run first, and cap with `--maximum_bytes_billed`.

## 1. Quality gate

### 1a. Duplicate check

GA4 export is at-least-once delivery, so duplicates inflate counts:

```sql
SELECT COUNT(*) AS events,
       COUNT(DISTINCT CONCAT(CAST(event_timestamp AS STRING), '|', event_name,
                             CAST(event_bundle_sequence_id AS STRING), '|', user_pseudo_id)) AS distinct_keys
FROM `{project_id}.{dataset}.events_*`
WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
```

- dup rate = (events - distinct_keys) / events. **PASS if ≤ 0.5%.**
- If `event_bundle_sequence_id` is absent from the schema, drop it from the key.

### 1b. Null / join-key check

```sql
SELECT COUNT(*) AS events,
       COUNTIF(user_pseudo_id IS NULL) AS null_pseudo_id,
       COUNTIF((SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') IS NULL) AS null_session,
       COUNTIF(ARRAY_LENGTH(event_params) = 0) AS zero_params,
       COUNTIF(user_id IS NOT NULL) AS with_user_id
FROM `{project_id}.{dataset}.events_*`
WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
```

- **PASS if null_pseudo_id and null_session shares ≤ 0.5%.**
- High `zero_params` on purchase/checkout events → tracking defect, not a funnel signal.
- `with_user_id` = 0 → no cross-device identity; every user-level number counts cookies, not people — state this in every user-based rate.

### 1c. Session integrity

```sql
WITH per_session AS (
  SELECT (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS session_id,
         COUNT(*) AS events, COUNT(DISTINCT event_name) AS types
  FROM `{project_id}.{dataset}.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
    AND (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') IS NOT NULL
  GROUP BY session_id
)
SELECT COUNT(*) AS sessions, AVG(events) AS avg_events,
       COUNTIF(events = 1) AS single_event_sessions, MAX(events) AS max_events,
       COUNTIF(events > 100) AS huge_sessions
FROM per_session
```

- **PASS if single-event share ≤ 10% and >100-event share ≤ 5%.** Otherwise
  sessionization is broken (fragmented or merged sessions) and sessions cannot
  be trusted for funnels.
- If `session_id` is a first-class column (newer exports) instead of the
  `ga_session_id` param, read it directly.

## 2. Dedupe pattern (use whenever dup rate > 0.5%)

```sql
WITH events_dedup AS (
  SELECT * EXCEPT(rn) FROM (
    SELECT *, ROW_NUMBER() OVER (
      PARTITION BY event_timestamp, event_name, event_bundle_sequence_id, user_pseudo_id
    ) AS rn
    FROM `{project_id}.{dataset}.events_*`
    WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
  ) WHERE rn = 1
)
SELECT ... FROM events_dedup ...
```

For non-GA4 sources (custom event tables), adapt the dedupe key to the columns
that uniquely identify a row.

## 3. Required-param coverage matrix

```sql
SELECT event_name,
  COUNT(*) AS n,
  COUNTIF(ARRAY_LENGTH(items) > 0) AS with_items,
  COUNTIF((SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'currency') IS NOT NULL) AS with_currency,
  COUNTIF((SELECT value.double_value FROM UNNEST(event_params) WHERE key = 'value') IS NOT NULL
       OR (SELECT value.float_value  FROM UNNEST(event_params) WHERE key = 'value') IS NOT NULL) AS with_value,
  COUNTIF((SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'transaction_id') IS NOT NULL) AS with_transaction_id,
  COUNTIF((SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'search_term') IS NOT NULL) AS with_search_term,
  COUNTIF((SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'item_list_name') IS NOT NULL) AS with_item_list_name
FROM `{project_id}.{dataset}.events_*`
WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
  AND event_name IN ('view_item','view_item_list','select_item','add_to_cart',
                     'remove_from_cart','view_cart','begin_checkout',
                     'add_shipping_info','add_payment_info','purchase','refund',
                     'search','view_search_results','view_promotion','select_promotion')
GROUP BY event_name ORDER BY n DESC
```

## 4. Expected params by event (the scoring matrix)

| event | required params |
| --- | --- |
| `search` / `view_search_results` | `search_term` |
| `view_item` / `select_item` / `select_promotion` | `items` |
| `view_item_list` | `items`, `item_list_name` |
| `add_to_cart` / `remove_from_cart` / `view_cart` | `items`, `currency`, `value` |
| `begin_checkout` / `add_shipping_info` / `add_payment_info` | `items`, `currency`, `value` |
| `purchase` / `refund` | `items`, `currency`, `value`, `transaction_id` |

A param counts as **present** only if coverage ≥ 95% for that event.

## 5. Items-array integrity

```sql
SELECT event_name, COUNT(*) AS n,
  COUNTIF(ARRAY_LENGTH(items) > 0) AS with_items,
  ROUND(100 * COUNTIF(ARRAY_LENGTH(items) > 0) / COUNT(*), 1) AS pct_with_items,
  ROUND(SUM(ARRAY_LENGTH(items)) / COUNT(*), 2) AS avg_items
FROM `{project_id}.{dataset}.events_*`
WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
  AND event_name IN ('view_item','view_item_list','select_item','add_to_cart',
                     'begin_checkout','add_shipping_info','add_payment_info','purchase')
GROUP BY event_name ORDER BY n DESC
```

- **DEFECT if any commerce event has < 95% with_items** (a step with 0% items —
  e.g. `add_payment_info` — breaks product-level analysis at that stage). The
  usual fix is forwarding the existing `ecommerce.items` dataLayer object,
  typically a GTM config change, not new app code.

## 6. List-impression consistency

```sql
SELECT
  SUM(IF(event_name = 'view_item_list', 1, 0)) AS list_impressions,
  SUM(IF(event_name = 'select_item', 1, 0)) AS item_clicks,
  SUM(IF(event_name = 'view_item', 1, 0)) AS item_views
FROM `{project_id}.{dataset}.events_*`
WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
```

- Rule: impressions ≥ clicks ≥ views.
- **FAIL if `view_item_list` < 10% of `select_item`** → list impressions are
  untracked and category/list-level analysis is blind.

## 7. Tracking health score (with worked example)

Score = `100 * (params present / params expected)`, where "expected" comes from
the matrix in §4 and a param counts as present only at ≥ 95% coverage. Only
count events that exist in the inventory. Present the per-event coverage table
(`event | expected params | % complete | defect`) alongside the score so the
number is auditable.

**Worked example — `ga4_obfuscated_sample_ecommerce`, Nov–Dec 2020
(2,744,994 events), observed coverage on that run:**

| event | items | value | transaction_id | search_term | defect |
| --- | --- | --- | --- | --- | --- |
| view_item | 59% | n/m | n/m | — | items < 95% |
| add_to_cart | 100% | n/m | n/m | — | none on items |
| begin_checkout | 72% | n/m | n/m | — | items < 95% |
| add_shipping_info | 0% | n/m | n/m | — | items missing (structural) |
| add_payment_info | 0% | n/m | n/m | — | items missing (structural) |
| purchase | 99.8% | 63% | 86% | — | value + transaction_id < 95% |
| select_item | ~100% | — | — | — | none |
| view_item_list | 68% | — | — | — | items < 95% (62 events vs 17,291 select_item) |
| view_search_results | — | — | — | 100% | none |

n/m = not measured on that run (currency/value weren't part of the items-coverage
audit) — the score reflects the params actually measured. That run scored
**≈ 45%**. The two structural defects to call out: checkout steps
(`add_shipping_info` / `add_payment_info`) drop `items`, and `view_item_list`
is essentially untracked.
