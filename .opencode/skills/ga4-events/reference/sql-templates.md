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

`value` can be stored as `int_value`, `float_value`, **or** `double_value` — check
all three or coverage is under-reported. In newer exports `value` and
`transaction_id` may also live in columns/structs (`event_value_in_usd`,
`ecommerce.transaction_id`) instead of `event_params`; confirm which exist via
`INFORMATION_SCHEMA.COLUMNS` and add them as `OR` fallbacks. **But** before
trusting `ecommerce.transaction_id`, run the §3b placeholder check — on some
exports it is a constant (cardinality 1) shipped on every event, and counting it
would inflate coverage to ~100% everywhere.

```sql
SELECT event_name,
  COUNT(*) AS n,
  COUNTIF(ARRAY_LENGTH(items) > 0) AS with_items,
  COUNTIF((SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'currency') IS NOT NULL) AS with_currency,
  COUNTIF((SELECT value.int_value    FROM UNNEST(event_params) WHERE key = 'value') IS NOT NULL
       OR (SELECT value.float_value  FROM UNNEST(event_params) WHERE key = 'value') IS NOT NULL
       OR (SELECT value.double_value FROM UNNEST(event_params) WHERE key = 'value') IS NOT NULL
       OR event_value_in_usd IS NOT NULL) AS with_value,
  COUNTIF((SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'transaction_id') IS NOT NULL
       OR ecommerce.transaction_id IS NOT NULL) AS with_transaction_id,
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

The event list above is the **ecommerce set**. For a SaaS property, substitute
the SaaS event list (§4b) instead — the coverage logic is identical, only the
event names change. In either case, adapt the list to what the property
actually emits: the matrix exists to score *tracking completeness* on real
events, not to validate against a fixed spec.

### 3b. Transaction-id hygiene (placeholder / wrong-event check)

`transaction_id` belongs on `purchase`/`refund` only. On some exports the
`ecommerce.transaction_id` column is a **constant placeholder** (same value on
every event) — it must be treated as absent, not as tracking. Check before
trusting `with_transaction_id` in §3:

```sql
SELECT event_name,
  COUNTIF(ecommerce.transaction_id IS NOT NULL) AS with_ecomm_txn,
  COUNT(DISTINCT IF(ecommerce.transaction_id IS NOT NULL, ecommerce.transaction_id, NULL)) AS distinct_ecomm_txn,
  COUNTIF((SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'transaction_id') IS NOT NULL) AS with_param_txn
FROM `{project_id}.{dataset}.events_*`
WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
GROUP BY event_name
HAVING with_ecomm_txn > 0
ORDER BY with_ecomm_txn DESC
```

- **Flag any event with `with_ecomm_txn` high but `distinct_ecomm_txn = 1`**
  → constant placeholder; drop `ecommerce.transaction_id` from that event's
  §3 coverage and rely on `with_param_txn`.
- **Flag `transaction_id` on non-purchase/refund events** → either the same
  placeholder, or the items dataLayer is being copied wholesale; either way it
  must not be read as real transactions.

## 4. Expected params by event (the scoring matrix)

Pick the matrix matching the property type (`{property_type}` = `ecommerce` or
`saas`). A param counts as **present** only if coverage ≥ 95% for that event.

### 4a. Ecommerce

| event | required params |
| --- | --- |
| `search` / `view_search_results` | `search_term` |
| `view_item` / `select_item` / `select_promotion` | `items` |
| `view_item_list` | `items`, `item_list_name` |
| `add_to_cart` / `remove_from_cart` / `view_cart` | `items`, `currency`, `value` |
| `begin_checkout` / `add_shipping_info` / `add_payment_info` | `items`, `currency`, `value` |
| `purchase` / `refund` | `items`, `currency`, `value`, `transaction_id` |

### 4b. SaaS

| event | required params |
| --- | --- |
| `sign_up` / `create_account` | `method` (google/email/sso), `user_id` |
| `login` | `method` |
| `generate_lead` | `form_id`, `value` (lead value) |
| `pricing_view` | `plan` / `item_name`, `currency` |
| `start_trial` / `trial_started` | `plan`, `trial_length`, `currency` |
| `subscribe` / `purchase` | `plan`, `currency`, `value`, `transaction_id` |
| `upgrade` / `downgrade` | `plan`, `new_plan`, `currency`, `value` |
| `cancel_subscription` / `churn` | `plan`, `reason` |
| `refund` | `value`, `currency`, `transaction_id` |
| `search` / `view_search_results` | `search_term` |
| `tutorial_complete` / `onboarding_complete` | `name` / `step` |

Use the `{property_type}` parameter consistently across the audit, funnel, and
scoring so the ecommerce and SaaS branches never mix. Items/transaction-id
integrity (§5, §6) apply to ecommerce; for SaaS the parallel checks are plan +
value + currency coverage on `subscribe`/`start_trial` (same SQL, different
event list).

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
(2,744,994 events), observed coverage on that run (measured with §3
including `int_value` and `event_value_in_usd`):**

| event | items | value | transaction_id | search_term | defect |
| --- | --- | --- | --- | --- | --- |
| view_item | 59% | n/m | n/m | — | items < 95% |
| add_to_cart | 100% | 0% | n/m | — | value missing |
| begin_checkout | 72% | 0% | n/m | — | items + value < 95% |
| add_shipping_info | 0% | 0% | n/m | — | items + value missing (structural) |
| add_payment_info | 0% | 0% | n/m | — | items + value missing (structural) |
| purchase | 99.8% | **97%** | **99%** | — | none (was value 63% / txn 86% before the §3 fix) |
| select_item | ~100% | — | — | — | none |
| view_item_list | 68% | — | — | — | items < 95% (62 events vs 17,291 select_item) |
| view_search_results | — | — | — | 100% | none |

n/m = not measured on that run (currency/value weren't part of the items-coverage
audit) — the score reflects the params actually measured. **Placeholder note:
`ecommerce.transaction_id` on this export is a constant (cardinality 1) on every
event** — only `purchase` carries a real distinct value (3,217 distinct), so
`transaction_id` coverage comes from §3b, not the raw column. That run scored
**≈ 41%** with the corrected value/transaction_id measurement. The two
structural defects to call out: checkout steps (`add_shipping_info` /
`add_payment_info`) drop `items` + `value`, and `view_item_list` is
essentially untracked.

## 8. SaaS macro funnel

For a SaaS property the funnel steps are account/plan events, not commerce
steps. Use the events that actually exist (from the inventory) and drop
missing ones; keep `session_start` as step 1. Adapt if `session_id` is a
first-class column vs the `ga_session_id` param.

```sql
WITH funnel AS (
  SELECT
    (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS session_id,
    MAX(IF(event_name = 'session_start', 1, 0)) AS s1,
    MAX(IF(event_name = 'sign_up', 1, 0))        AS s2,
    MAX(IF(event_name = 'create_account', 1, 0)) AS s3,
    MAX(IF(event_name = 'pricing_view', 1, 0))   AS s4,
    MAX(IF(event_name = 'start_trial', 1, 0))    AS s5,
    MAX(IF(event_name = 'subscribe', 1, 0))      AS s6,
    MAX(IF(event_name = 'purchase', 1, 0))       AS s7
  FROM `{project_id}.{dataset}.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
    AND (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') IS NOT NULL
  GROUP BY session_id
)
SELECT COUNT(*) AS sessions, SUM(s1) AS session_start, SUM(s2) AS sign_up,
       SUM(s3) AS create_account, SUM(s4) AS pricing_view, SUM(s5) AS start_trial,
       SUM(s6) AS subscribe, SUM(s7) AS purchase
FROM funnel
```

- `sign_up` and `create_account` are often the same act under two names — if
  both fire, canonicalize (see §3b-style duplicate check) rather than counting
  two steps.
- `subscribe`/`purchase` are the macro conversion for SaaS. If the property
  uses `subscribe`, use it in place of `purchase` in every downstream query
  (scoring, segmentation) for consistency.
- Trial-era SaaS often has `start_trial` and `subscribe` close together in
  time; report the *median days between* `start_trial` and `subscribe` as a
  supplementary metric, not a funnel step.
