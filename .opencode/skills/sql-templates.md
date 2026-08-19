# Shared SQL templates & thresholds (GA4) — discovery-first

Single source of truth for the SQL and thresholds used by `ga4-events`,
`ga4-tracking-audit`, `ga4-segmentation`, `ga4-kpi-snapshot`,
`ga4-retention-cohorts`, and `ga4-tracking-trend`. Edit templates in this file,
never inline in a SKILL.md.

## Core principle: discovery, not spec

**No event name is hardcoded anywhere.** The skills discover what actually
exists in the export and build everything (inventory, funnel steps, param
coverage, recommendations) from the observed data. The **role vocabulary**
(§4) is a *heuristic prior* used to label observed events and to *suggest*
what's missing — it is never treated as a list of events that must exist.

Rules that apply to every skill:

1. **Run the discovery queries (§1) first.** Everything else derives from what
   you find, including the property type.
2. **Never assume an event exists** because the vocabulary mentions it. If the
   inventory has no matching event for a role, report it as a *suggested
   addition* with the funnel stage it would unblock — not as a measured step.
3. **Never ignore an event the vocabulary doesn't list.** Classify unknown
   events by their parameter evidence and report the inference you made.
4. **Report the mapping.** When you map events to roles, print the
   `event → role → evidence` table so the reader can see (and correct) the
   inference. Every funnel number must be traceable to the event name that
   produced it.
5. **Recommendations are suggestions.** Phrase every ADD/FIX/REMOVE as a
   recommendation tied to an observed gap (missing role, low coverage,
   duplicate page mapping). If the data is already complete for that role,
   say so and don't manufacture a fix.

## Parameters (substitute per run)

- `{project_id}` — billing project / where the export lives
- `{dataset}` — e.g. `analytics_123456789`
- `{table}` — GA4 export prefix: `events_*` (covers `events_YYYYMMDD` and
  `events_intraday_*`). Always filter with `_TABLE_SUFFIX BETWEEN '{start}'
  AND '{end}'`.
- `{start}` / `{end}` — date window in YYYYMMDD, e.g. `'20260701'` ..
  `'20261001'`
- `{property_type}` — **optional override** (`ecommerce` or `saas`). If not
  supplied, the skill infers it from the inventory (§4b). The inference is
  reported; the override only changes the funnel step set used.
- `{segment_dim}` — for `ga4-segmentation` only.

## Cost-safety (always, before any query)

- GA4 export is **date-partitioned**: always filter with `_TABLE_SUFFIX BETWEEN ...` or `event_date`. Never scan `events_*` unfiltered.
- Run a **dry run** first to check bytes scanned before paying for the real run. **Caveat:** a dry-run against a `events_*` wildcard can report `totalBytesProcessed: 0` (misleading) — the real run will scan the data, so still set a cap.
- Set `--maximum_bytes_billed` so a runaway query fails instead of billing. Start at `--maximum_bytes_billed=2000000000` (2 GB) and tighten after seeing the first real-run byte count.
- `SELECT` only needed columns; avoid `SELECT *` on wide exports.
- Repeated identical queries hit the free 24h cache.

## 1. Discovery (run first; everything derives from this)

### 1a. Event inventory with evidence

```sql
SELECT event_name,
  COUNT(*) AS n,
  COUNT(DISTINCT user_pseudo_id) AS users,
  COUNT(DISTINCT (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_location')) AS distinct_pages,
  COUNTIF(ARRAY_LENGTH(event_params) = 0) AS zero_params,
  COUNTIF((SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') IS NOT NULL) AS with_session
FROM `{project_id}.{dataset}.events_*`
WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
GROUP BY event_name
ORDER BY n DESC
```

- `distinct_pages` shows what each event fires on (used for duplicate/misconfig
  detection and page-level classification).
- `with_session` < ~95% on session-scoped events is itself a defect.
- If the schema has first-class columns (`session_id`, `event_value_in_usd`,
  `ecommerce`, `page_location`) instead of/alongside params, confirm via
  `INFORMATION_SCHEMA.COLUMNS` and read them directly.

### 1b. Parameter map per event

What params each event actually carries — the raw material for coverage checks.

```sql
SELECT event_name, p.key AS param_key, COUNT(*) AS n
FROM `{project_id}.{dataset}.events_*`, UNNEST(event_params) AS p
WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
GROUP BY event_name, p.key
ORDER BY event_name, n DESC
```

- Coverage of param `k` on event `e` = `n(e,k) / n(e)` from §1a. Don't run
  per-param COUNTIF queries; compute from these two tables.
- `value` may be stored as `int_value`/`float_value`/`double_value`; the param
  map counts the key regardless of type. If the export has an
  `event_value_in_usd` column, count it as value evidence too.
- Custom/renamed params will show up here — if a role-mapped event carries a
  param the vocabulary doesn't mention, note it and treat it as its value
  evidence.

### 1c. Page map per event (top page_locations)

For duplicate-event and misconfiguration detection:

```sql
SELECT event_name, p.value.string_value AS page_location, COUNT(*) AS n
FROM `{project_id}.{dataset}.events_*`, UNNEST(event_params) AS p
WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
  AND p.key = 'page_location'
GROUP BY event_name, page_location
ORDER BY event_name, n DESC
```

Two distinct events firing on the same top page_location = duplicate/rename
candidate. An event mapped to the wrong page = misconfiguration.

### 1d. Session-key check

```sql
SELECT DISTINCT column_name FROM `{project_id}.{dataset}.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name LIKE 'events_%' AND column_name IN ('session_id','user_id','page_location','event_value_in_usd')
ORDER BY column_name
```

Note: without `DISTINCT`, the query returns one row per (daily table × column) and
`user_id` gets buried under `event_value_in_usd` noise. `event_date` is a STRING
in GA4 exports (YYYYMMDD) — treat it as text in date math or `PARSE_DATE` it.

If `session_id` is a first-class column, use it directly everywhere instead of
the `ga_session_id` param. Same for `user_id` (identity checks) and
`page_location` (page maps).

## 2. Quality gate

Data-agnostic; run before trusting any finding. If a check fails, report it —
never silently present funnel numbers on unclean data.

### 2a. Duplicate check

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

### 2b. Null / join-key check

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
- High `zero_params` on a conversion event → tracking defect, not a funnel signal.
- `with_user_id = 0`:
  - for **ecommerce** → note it: every user-level number counts cookies, not people.
  - for **saas** → this is a **defect**: logged-in SaaS users should carry
    `user_id`. Report it as a code-side identity fix, not a neutral note.

### 2c. Session integrity

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
- If `session_id` is a first-class column (newer exports), read it directly.

## 3. Dedupe pattern (use whenever dup rate > 0.5%)

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

## 4. Role vocabulary & property-type inference

### 4a. Role vocabulary (heuristic prior — NOT a spec)

Used to label observed events and to *suggest* missing stages. Patterns are
matched against the discovered `event_name` (case-insensitive substring). Each
role carries the params that event is *meaningfully expected* to carry — these
are standard GA4/analytics param names; if the param map shows the event uses
different names, use what it actually carries.

| role | name patterns (contains) | meaningful params | funnel set |
| --- | --- | --- | --- |
| visit / session | `session_start`, `first_visit` | — | common |
| engagement (noise) | `user_engagement`, `scroll`, `click` | — | common |
| page view (marker) | `page_view`, `view` | — | common |
| signup | `sign_up`, `signup`, `create_account`, `register`, `account_created` | `method`, `user_id` | saas |
| login | `login`, `log_in`, `signed_in` | `method` | saas |
| lead | `generate_lead`, `lead`, `demo_request`, `contact_sent`, `form_submit` | `form_id`, `value` | saas |
| pricing view | `pricing_view`, `view_pricing`, `plan_view`, `price_view` | `plan`, `currency`, `item_name` | saas |
| trial start | `start_trial`, `trial_started`, `free_trial`, `trial_begin` | `plan`, `trial_length`, `currency` | saas |
| subscribe / purchase | `subscribe`, `subscription`, `purchase`, `paid`, `plan_paid`, `checkout_success`, `order_confirmed` | `plan`, `currency`, `value`, `transaction_id` | saas + common |
| upgrade / downgrade | `upgrade`, `downgrade`, `plan_change` | `plan`, `new_plan`, `currency`, `value` | saas |
| cancel / churn | `cancel_subscription`, `churn`, `unsubscribe`, `cancel` | `plan`, `reason` | saas |
| activation | `tutorial_complete`, `onboarding_complete`, `activated`, `first_project`, `welcome_done` | `name`, `step` | saas |
| search | `search`, `view_search_results` | `search_term` | common |
| product view | `view_item`, `product_view`, `item_view`, `view_product` | `items` | ecommerce |
| list view | `view_item_list`, `category_view`, `list_view` | `items`, `item_list_name` | ecommerce |
| item select | `select_item`, `item_click`, `product_click` | `items` | ecommerce |
| cart add / remove | `add_to_cart`, `addtocart`, `remove_from_cart`, `view_cart` | `items`, `currency`, `value` | ecommerce |
| checkout begin | `begin_checkout`, `checkout`, `checkout_start` | `items`, `currency`, `value` | ecommerce |
| payment info | `add_payment_info`, `add_shipping_info`, `payment` | `items`, `currency`, `value` | ecommerce |
| promotion | `view_promotion`, `select_promotion` | `promotion_id`, `creative_name`, `items` | common |

- `value` on subscribe/purchase is a **first-payment** signal only; for SaaS,
  recurring MRR lives in Stripe, not GA4 — state that when you report SaaS
  revenue gaps instead of demanding full `value` coverage.
- If `sign_up` and `create_account` both map to signup, treat them as one
  canonical stage (report the duplicate) rather than two funnel steps.

### 4b. Property-type inference (report it)

If `{property_type}` is not supplied, infer from the inventory:

- **saas** if any of these roles have an observed event: signup, login, lead,
  pricing view, trial start, subscribe, upgrade/downgrade, cancel, activation.
- **ecommerce** if not, and product/cart/checkout roles have observed events.
- Ambiguous → report the events that made you choose, and note the other
  funnel set could be layered on.

Report the inference and its evidence in the output. The funnel set then
selects which vocabulary roles are *expected*; the funnel itself is built only
from events that actually exist (§5).

## 5. Funnel construction (data-driven)

### 5a. Selecting funnel steps

1. From §1a inventory, keep events that map to a non-noise role (§4a).
2. For each expected role (in the canonical order below), pick the best-matching
   observed event (highest `n`, name-pattern match preferred over param-only).
3. Report the `step → event_name` mapping before showing any rate.
4. Roles with **no** observed event are **suggested additions** — they are not
   funnel steps, and their absence is reported in Recommendations, not silently
   skipped.

Canonical order: visit → signup → activation → pricing → trial → subscribe
(saas); visit → product view → list/select → cart → checkout → payment →
purchase (ecommerce). `session_start` is always step 1.

### 5b. Session funnel template

Substitute the **discovered** event names for `{step2}` … `{stepN}`:

```sql
WITH funnel AS (
  SELECT
    (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS session_id,
    MAX(IF(event_name = 'session_start', 1, 0)) AS s1,
    MAX(IF(event_name = '{step2}', 1, 0)) AS s2,
    MAX(IF(event_name = '{step3}', 1, 0)) AS s3,
    MAX(IF(event_name = '{step4}', 1, 0)) AS s4,
    MAX(IF(event_name = '{step5}', 1, 0)) AS s5,
    MAX(IF(event_name = '{step6}', 1, 0)) AS s6
  FROM `{project_id}.{dataset}.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
    AND (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') IS NOT NULL
  GROUP BY session_id
)
SELECT COUNT(*) AS sessions, SUM(s1) AS step1, SUM(s2) AS step2, SUM(s3) AS step3,
       SUM(s4) AS step4, SUM(s5) AS step5, SUM(s6) AS step6
FROM funnel
```

- Report per-step drop-off and step-to-step conversion. Always report
  `session_start` as step 1 even when it equals sessions.
- **Step-through sanity check:** a mid-funnel step that retains >90% of the
  previous step usually fires on page load rather than on the user action —
  verify its `page_location` (from §1c) before reporting it as a real
  conversion step.

### 5c. User-level funnel (SaaS flows span sessions)

For SaaS, signup → trial → subscribe happens across days/sessions. Use a
**per-user** funnel keyed on first occurrence of each step:

```sql
WITH per_user AS (
  SELECT user_pseudo_id,
    MIN(IF(event_name = 'session_start', event_timestamp, NULL)) AS t1,
    MIN(IF(event_name = '{step2}', event_timestamp, NULL)) AS t2,
    MIN(IF(event_name = '{step3}', event_timestamp, NULL)) AS t3,
    MIN(IF(event_name = '{step4}', event_timestamp, NULL)) AS t4,
    MIN(IF(event_name = '{step5}', event_timestamp, NULL)) AS t5,
    MIN(IF(event_name = '{step6}', event_timestamp, NULL)) AS t6
  FROM `{project_id}.{dataset}.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
  GROUP BY user_pseudo_id
)
SELECT COUNT(*) AS users,
  COUNTIF(t1 IS NOT NULL) AS step1, COUNTIF(t2 IS NOT NULL) AS step2,
  COUNTIF(t3 IS NOT NULL) AS step3, COUNTIF(t4 IS NOT NULL) AS step4,
  COUNTIF(t5 IS NOT NULL) AS step5, COUNTIF(t6 IS NOT NULL) AS step6
FROM per_user
```

- Report step-to-step conversion from this table.
- If `user_id` is populated (SaaS), key on it instead and state that the funnel
  counts *users*, not cookies.
- If both `start_trial` and `subscribe` exist, report the **median days
  between them** as a supplementary metric:
  `APPROX_QUANTILES(DATE_DIFF(DATE(TIMESTAMP_MICROS(t_sub)), DATE(TIMESTAMP_MICROS(t_trial)), DAY), 100)[OFFSET(50)]`.

## 6. Coverage assessment (from the param map)

1. For every role-mapped event, look up its **meaningful params** in §4a.
2. Coverage = `n(event, param) / n(event)` (from §1a + §1b).
3. Report a per-event coverage table:
   `event | role | params measured | % coverage | defect`.
4. **DEFECT** if a meaningful param has < 95% coverage on its event. 0% on a
   conversion event = structural (breaks revenue/plan analysis at that stage).

### 6a. Transaction-id placeholder check

Run when `subscribe`/`purchase`-role events exist:

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
  → constant placeholder; drop it from coverage, rely on `with_param_txn`.
- **Flag `transaction_id` on non-purchase/subscribe events** → placeholder or
  copied dataLayer; never read as real transactions.

## 7. Tracking health score

Score = `100 * (params present / params expected)`, where "expected" = the
meaningful params (§4a) of every **observed** role-mapped event, and a param
counts as present only at ≥ 95% coverage. Only count events that exist in the
inventory. Present the per-event coverage table alongside the score.

Missing roles are **not** scored as defects (they are suggestions, §5a) — but
report how many expected roles have no observed event so the score is read in
context.

## 8. KPI snapshot templates (`ga4-kpi-snapshot`)

### 8a. Daily active users + sessions + engagement

```sql
SELECT event_date,
  COUNT(DISTINCT user_pseudo_id) AS dau,
  COUNT(DISTINCT (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id')) AS sessions,
  COUNT(DISTINCT IF(event_name = 'user_engagement',
    (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id'), NULL)) AS engaged_sessions
FROM `{project_id}.{dataset}.events_*`
WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
GROUP BY event_date
ORDER BY event_date
```

- engagement rate = engaged_sessions / sessions (per day and averaged).
- If `session_id` is a column, read it directly.

### 8b. WAU / MAU / stickiness

```sql
WITH users AS (
  SELECT user_pseudo_id,
    MAX(event_date) AS last_active_date,
    MIN(event_date) AS first_active_date
  FROM `{project_id}.{dataset}.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
  GROUP BY user_pseudo_id
)
SELECT
  COUNT(*) AS total_users,
  COUNTIF(PARSE_DATE('%Y%m%d', last_active_date) >= DATE_SUB(DATE '{end_date}', INTERVAL 6 DAY)) AS wau,
  COUNTIF(PARSE_DATE('%Y%m%d', last_active_date) >= DATE_SUB(DATE '{end_date}', INTERVAL 29 DAY)) AS mau
FROM users
```

- `{end_date}` = `'{end}'` formatted as `YYYY-MM-DD` (e.g. `2026-10-01`).
- **`event_date` is a STRING (YYYYMMDD) in GA4 exports** — always `PARSE_DATE`
  before comparing it to a DATE; `last_active_date >= DATE_SUB(...)` fails
  otherwise (tested on the public sample). stickiness = wau / mau (and dau / mau
  using avg dau from 8a). Report both.

### 8c. New vs returning (cookie-based)

```sql
SELECT
  COUNTIF((SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_number') = 1) AS new_sessions,
  COUNTIF((SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_number') > 1) AS returning_sessions,
  COUNT(DISTINCT user_pseudo_id) AS users
FROM `{project_id}.{dataset}.events_*`
WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
  AND event_name = 'session_start'
```

State that this is cookie-based when `user_id` is empty.

### 8d. WoW / MoM deltas

Run the same queries on the previous equal-length window (shift `{start}`/`{end}`
back by the window length) and report % change for dau avg, mau, wau,
engagement rate, and the macro conversion step. Label the comparison window.

## 9. Retention cohorts (`ga4-retention-cohorts`)

### 9a. New-user retention by cohort week

```sql
WITH cohorts AS (
  SELECT
    user_pseudo_id,
    DATE_TRUNC(DATE(TIMESTAMP_MICROS(user_first_touch_timestamp)), WEEK(MONDAY)) AS cohort_week
  FROM `{project_id}.{dataset}.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
    AND user_first_touch_timestamp IS NOT NULL
  GROUP BY user_pseudo_id, cohort_week
),
active AS (
  SELECT DISTINCT
    user_pseudo_id,
    DATE_TRUNC(PARSE_DATE('%Y%m%d', event_date), WEEK(MONDAY)) AS active_week
  FROM `{project_id}.{dataset}.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
)
SELECT
  c.cohort_week,
  COUNT(DISTINCT c.user_pseudo_id) AS cohort_size,
  COUNTIF(DATE_DIFF(a.active_week, c.cohort_week, WEEK) = 0) AS wk0,
  COUNTIF(DATE_DIFF(a.active_week, c.cohort_week, WEEK) = 1) AS wk1,
  COUNTIF(DATE_DIFF(a.active_week, c.cohort_week, WEEK) = 2) AS wk2,
  COUNTIF(DATE_DIFF(a.active_week, c.cohort_week, WEEK) = 3) AS wk3,
  COUNTIF(DATE_DIFF(a.active_week, c.cohort_week, WEEK) = 4) AS wk4
FROM cohorts c
LEFT JOIN active a ON a.user_pseudo_id = c.user_pseudo_id
GROUP BY c.cohort_week
ORDER BY c.cohort_week
```

- Report retention % (wkN / cohort_size) per cohort; keep cohorts with < ~100
  users flagged as noise. "Active" = ≥1 event that week (uses the discovered
  inventory; if the property defines activity more strictly, say so).

### 9b. Activation / trial-to-paid (uses discovered step events)

Reuse the user-level funnel (§5c): activation rate = users reaching the
`activation` role event ÷ signup users. Trial→paid = median days between
`{trial_event}` and `{subscribe_event}` (both discovered). Report per-cohort if
sample size allows.

### 9c. Churn proxy

```sql
SELECT
  COUNT(DISTINCT IF(event_name = '{cancel_event}', user_pseudo_id, NULL)) AS churners,
  COUNT(DISTINCT user_pseudo_id) AS users
FROM `{project_id}.{dataset}.events_*`
WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
```

- `{cancel_event}` = the discovered cancel/churn event (§4a). If none exists,
  report that churn is untracked and suggest the event.
- Caveat: GA4 churn is event-based; true cancellation data lives in Stripe.
  State this when you report it.

## 10. Tracking-trend templates (`ga4-tracking-trend`)

Compare the quality gate and health score **across successive windows** to catch
silent regressions. For each window bucket, run §2 checks and §7 score. Compact
per-week gate query:

```sql
WITH per_bucket AS (
  SELECT
    DATE_TRUNC(DATE(TIMESTAMP_MICROS(event_timestamp)), WEEK(MONDAY)) AS bucket,
    event_timestamp, event_name, event_bundle_sequence_id, user_pseudo_id,
    (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS session_id,
    ARRAY_LENGTH(event_params) AS n_params
  FROM `{project_id}.{dataset}.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
)
SELECT
  bucket,
  COUNT(*) AS events,
  COUNT(DISTINCT CONCAT(CAST(event_timestamp AS STRING), '|', event_name,
                        CAST(event_bundle_sequence_id AS STRING), '|', user_pseudo_id)) AS distinct_keys,
  COUNTIF(user_pseudo_id IS NULL) AS null_pseudo,
  COUNTIF(session_id IS NULL) AS null_session,
  COUNTIF(n_params = 0) AS zero_params
FROM per_bucket
GROUP BY bucket
ORDER BY bucket
```

- Report per bucket: events, dup rate, null shares, zero-param share. A bucket
  that jumps vs its neighbors = a regression window (deploy / GTM change).
- Pair with the health score (§7) per bucket for the same window.
- **Flag any bucket that deviates >2pp from the window median** on dup rate or
  null-session share; correlate with the property's release calendar if known.

## PowerShell (Windows) querying pattern

`bq.cmd` mangles quotes and backticks. Reliable pattern:

```powershell
$env:Path = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin;" + $env:Path
& "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\bq.cmd" query '--project_id=PROJECT' '--use_legacy_sql=false' '--format=pretty' "SELECT event_name FROM ``{project_id}.{dataset}.events_*`` WHERE _TABLE_SUFFIX BETWEEN '20260701' AND '20261001'"
```

- PowerShell **double-quoted** string + **doubled backticks** (``) for BigQuery identifiers.
- SQL string literals with **single quotes** (`'20260701'`); double quotes get stripped by the cmd wrapper.
- Dry run first, then add `--maximum_bytes_billed=2000000000` (2 GB) to the real run.
- Always pass `--project_id` (no default project is set).