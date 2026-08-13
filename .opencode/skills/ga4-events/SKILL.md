---
name: ga4-events
description: Analyze GA4 (Google Analytics 4) raw BigQuery export events — event inventory, event hygiene classification (indicative vs noise), conversion funnel, and recommendations on events to add/remove. Produces a standardized report (quality gate, event classification, funnel, event add/fix/remove recommendations, data gaps and suggested improvements). Use when the user asks about GA4 events, event quality or hygiene, which GA4 events matter, funnel analysis on GA4 data, what events to track, or data gaps.
---

# GA4 Events Analysis

Analyze the raw GA4 event export in BigQuery and classify each `event_name`
as **indicative/useful** or **noise**, build a conversion funnel, and audit
event hygiene. Parameterized so it works on any GA4 export.

## Parameters (substitute per run)

- `{project_id}` — billing project / where the export lives
- `{dataset}` — e.g. `analytics_123456789`
- `{table}` — GA4 export prefix: `events_*` (covers `events_YYYYMMDD` and `events_intraday_*`). Always filter with `_TABLE_SUFFIX BETWEEN '{start}' AND '{end}'`.
- `{start}` / `{end}` — date window in YYYYMMDD, e.g. `'20260701'` .. `'20261001'`

## Reference schema (raw GA4 export, web)

| column | notes |
| --- | --- |
| `event_name` | STRING, the event |
| `_TABLE_SUFFIX` / `event_date` | YYYYMMDD; the table is date-partitioned |
| `event_timestamp` | INTEGER microseconds |
| `event_params` | ARRAY<STRUCT<key, value>>; extract with `(SELECT value.int_value|string_value FROM UNNEST(event_params) WHERE key = '...')` |
| `user_pseudo_id` | STRING, cookie/app id |
| `session_id` | STRING in newer exports; otherwise the `ga_session_id` event param |
| `items` | ARRAY<STRUCT<item_id, item_name, item_category, price, quantity, ...>> — populated on view_item/add_to_cart/purchase etc. |
| `device` | STRUCT<category, mobile_brand_name, operating_system, web_info.browser, ...> |
| `geo` | STRUCT<country, region, city> |
| `traffic_source` | STRUCT<name, medium, source> (plus `collected_traffic_source` in newer exports) |
| `page_location`, `page_title`, `page_referrer` | web page fields (columns in newer exports, else event params) |

Verify the actual schema before assuming columns:

```sql
SELECT column_name FROM `{project_id}.{dataset}.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name LIKE 'events_%' ORDER BY ordinal_position
```

## Cost-safety (always, before any query)

- GA4 export is **date-partitioned**: always filter with `_TABLE_SUFFIX BETWEEN ...` or `event_date`. Never scan `events_*` unfiltered.
- Run a **dry run** first to check bytes scanned before paying for the real run (see PowerShell pattern below).
- Set `--maximum_bytes_billed` (e.g. 500000000 = 500 MB) so a runaway query fails instead of billing.
- `SELECT` only needed columns; avoid `SELECT *` on wide exports.
- Repeated identical queries hit the free 24h cache.

## Quality gate (run before trusting any finding)

Verify the data passes these checks before reporting any rate. If a check
fails, report it — never silently present funnel numbers on unclean data.

1. **Duplicate check** — GA4 export is at-least-once delivery, so
   duplicates inflate counts:
   ```sql
   SELECT COUNT(*) AS events,
          COUNT(DISTINCT CONCAT(CAST(event_timestamp AS STRING), '|', event_name,
                                CAST(event_bundle_sequence_id AS STRING), '|', user_pseudo_id)) AS distinct_keys
   FROM `{project_id}.{dataset}.events_*`
   WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
   ```
   - dup rate = (events - distinct_keys) / events. If > 0.5%, use the dedupe
     pattern below in every downstream query.
   - If `event_bundle_sequence_id` is absent from the schema, drop it from the key.

2. **Null / join-key check**:
   ```sql
   SELECT COUNT(*) AS events,
          COUNTIF(user_pseudo_id IS NULL) AS null_pseudo_id,
          COUNTIF((SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') IS NULL) AS null_session,
          COUNTIF(ARRAY_LENGTH(event_params) = 0) AS zero_params,
          COUNTIF(user_id IS NOT NULL) AS with_user_id
   FROM `{project_id}.{dataset}.events_*`
   WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
   ```
   - `null_pseudo_id` or `null_session` share > 0.5% → user/session join keys
     are broken; report the defect, don't proceed as if sessions are complete.
   - High `zero_params` on purchase/checkout events → missing tracking, not a
     funnel signal.
   - `with_user_id` = 0 → the export has **no cross-device identity**; every
     user-level number counts cookies, not people. State this whenever you
     report user-based rates (and re-check for thelook-style tables where
     `user_id` may only exist on buyers).

3. **Session integrity**:
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
          COUNTIF(events = 1) AS single_event_sessions, MAX(events) AS max_events
   FROM per_session
   ```
   - Very high share of single-event sessions or an absurd `max_events` →
     sessionization is broken (fragmented or merged sessions); sessions cannot
     be trusted for funnels.

**Dedupe pattern for downstream queries** (use whenever dup rate > 0.5%):

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

For non-GA4 sources (e.g. custom event tables), adapt the dedupe key to the
columns that uniquely identify a row, and the join-key checks to their
user/session columns.

## Parameter & items coverage (tracking completeness)

Zero-param counts hide *partial* tracking. Run a required-param coverage
matrix per event to find steps that fire but carry no product/value data:

```sql
SELECT event_name,
  COUNT(*) AS n,
  COUNTIF(ARRAY_LENGTH(items) > 0) AS with_items,
  COUNTIF((SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'currency') IS NOT NULL) AS with_currency,
  COUNTIF((SELECT value.double_value FROM UNNEST(event_params) WHERE key = 'value') IS NOT NULL
       OR (SELECT value.float_value  FROM UNNEST(event_params) WHERE key = 'value') IS NOT NULL) AS with_value,
  COUNTIF((SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'transaction_id') IS NOT NULL) AS with_transaction_id,
  COUNTIF((SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'search_term') IS NOT NULL) AS with_search_term
FROM `{project_id}.{dataset}.events_*`
WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
  AND event_name IN ('view_item','view_item_list','select_item','add_to_cart',
                     'remove_from_cart','begin_checkout','add_shipping_info',
                     'add_payment_info','purchase','refund','search','view_search_results')
GROUP BY event_name ORDER BY n DESC
```

Expected coverage (treat shortfalls as tracking defects, not behavior):

- `items` on every commerce step: `view_item`/`view_item_list`/`select_item`,
  `add_to_cart`/`remove_from_cart`, `begin_checkout`/`add_shipping_info`/
  `add_payment_info`, `purchase`, `refund`. **A step with 0% items (e.g.
  `add_payment_info`) breaks product-level analysis at that stage** — the fix
  is forwarding the same `ecommerce.items` dataLayer object, usually a GTM
  config change, not new app code.
- `currency` + `value` on all commerce events (value can also be the
  `event_value_in_usd` column).
- `transaction_id` on `purchase`/`refund`.
- `search_term` on `search`/`view_search_results`.

Also check **list-impression vs click consistency**: `view_item_list` (and
`select_item`/`view_item`) should show list impressions ≥ clicks ≥ item views.
If `view_item_list` is ~0 while `select_item`/`view_item` are large, list
impressions are untracked and category/list-level analysis is blind.

## Attribution artifacts to flag

- `(data deleted)`, `(not set)`, `(unknown)` / `<Other>` traffic sources and
  self-referral domains (e.g. `shop.ownstore.com`) often show inflated
  conversion. Treat them as artifacts, not channel wins — don't shift budget
  on them.
- `traffic_source` values with no `source`/`medium` row columns populated
  (older exports) need `collected_traffic_source` instead.

## Event classification rubric

**Indicative / useful** (signals of intent or outcome):

- `purchase` — conversion; the primary success metric. Always useful.
- `add_payment_info`, `begin_checkout` — checkout-friction signals; pinpoint the leak between cart and purchase.
- `add_to_cart`, `remove_from_cart`, `add_to_wishlist` — product-intent micro-steps.
- `select_item`, `view_item`, `view_item_list` — which products get interest and convert.
- `search` (with `search_term` param) — explicit intent; a strong conversion predictor.
- `refund` (with amount) — negative outcome; return / cancellation risk.
- SaaS/lead events: `sign_up`, `login`, `generate_lead`, `pricing_view`, `subscribe`, `tutorial_complete` (or equivalent onboarding events) — the macro funnel for SaaS.

**Noise / low value**:

- `page_view`, `scroll`, generic `click` — volume with weak intent; keep for page-level UX, exclude from intent scoring.
- `session_start`, `first_visit` — session markers only, not intent.
- Custom per-page events that duplicate `page_view` (e.g. a `home_view` event) — remove; use `page_view` + `page_location`.
- Any event with **no event_params** and no downstream consumer.

**Context-dependent:**

- `page_view` + `page_location` is the signal when the question is about *which pages/products*.
- `traffic_source`, `geo`, `device` carry the signal when the question is about *who the user is*.

## Workflow

1. **Cost-safe setup**: pick the date window; dry-run the first query.
2. **Quality gate**: run the duplicate / null-key / session-integrity checks above. Record PASS/FAIL for each. If the dup rate > 0.5%, run the rest of the workflow against the dedupe pattern.
3. **Event inventory**:
   ```sql
   SELECT event_name, COUNT(*) AS n, COUNT(DISTINCT user_pseudo_id) AS users,
          COUNT(DISTINCT (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_location')) AS distinct_pages
   FROM `{project_id}.{dataset}.events_*`
   WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
   GROUP BY event_name ORDER BY n DESC
   ```
4. **Map events to pages**: for each event_name, check the top `page_location` values to confirm what it fires on (finds misconfigured/duplicate events).
5. **Session funnel** (adapt if `session_id` is a column vs the `ga_session_id` param):
   ```sql
   WITH funnel AS (
     SELECT
       (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS session_id,
       MAX(IF(event_name = 'session_start', 1, 0)) AS s1,
       MAX(IF(event_name = 'view_item', 1, 0)) AS s2,
       MAX(IF(event_name = 'add_to_cart', 1, 0)) AS s3,
       MAX(IF(event_name = 'begin_checkout', 1, 0)) AS s4,
       MAX(IF(event_name = 'add_payment_info', 1, 0)) AS s5,
       MAX(IF(event_name = 'purchase', 1, 0)) AS s6
     FROM `{project_id}.{dataset}.events_*`
     WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
     GROUP BY session_id
   )
   SELECT COUNT(*) AS sessions, SUM(s2) AS view_item, SUM(s3) AS add_to_cart,
          SUM(s4) AS begin_checkout, SUM(s5) AS add_payment_info, SUM(s6) AS purchase
   FROM funnel
   ```
   Report per-step drop-off and conversion rates (step n+1 / step n).
6. **Hygiene audit**:
   - run the **parameter & items coverage matrix** (above) — the biggest
     source of "event exists but is useless" findings;
   - events with zero params (dead weight / missing tracking);
   - custom events that duplicate recommended GA4 events (both firing on the same `page_location`);
   - events with absurd cardinality or a single value (debug/bucket leftovers);
   - recommended events that are missing (funnel gaps → what to add).
7. **Classify + recommend**: cite observed counts, list what to keep / add / remove / rename, and which funnel stage each gap hides. Always report the quality-gate result (PASS/FAIL + dup rate + null-key shares) alongside the findings.

## Standardized output

Always produce the report in this fixed order with these headers:

1. `Quality gate` — PASS/FAIL per check + dup rate + null-key shares + session-integrity notes.
2. `Event inventory & classification` — table of `event_name | count | distinct users | distinct pages | classification | gap notes`.
3. `Session funnel` — `step | sessions | % of sessions | step-to-step drop-off`; flag the single biggest leak.
4. `Recommendations (events)` — ADD / FIX / REMOVE, each tied to a gap or missing funnel stage.
5. `Data gaps & suggested improvements` — tracking holes + concrete fixes.

## Event recommendations

**Add when missing** (check the inventory first — every recommendation must
cite the observed funnel gap it fixes):

- Checkout micro-steps when the cart->purchase leak is the biggest:
  `begin_checkout`, `add_shipping_info`, `add_payment_info` (with
  `payment_method`), `checkout_progress`, `purchase_error`.
- Intent events: `search` (with `search_term`), `view_item_list` with
  impressions, `select_promotion` / `view_promotion` for campaign measurement.
- Negative outcomes: `remove_from_cart`, `refund` (with `amount` +
  `transaction_id`), `cart_abandon` (or derive from session funnel instead).
- Commerce params on every product event: `currency`, `value`, `quantity`,
  `item_category` — without them revenue and product-level funnels are empty.
- SaaS/lead macro funnel: `generate_lead`, `sign_up`, `pricing_view`,
  `tutorial_complete`.

**Fix**:

- Canonicalize renamed/duplicate events (e.g. both `addtocart` and
  `add_to_cart` firing on the same `page_location`) — merge, don't duplicate.
- Add `currency`/`value` when purchase totals are always 0 or NULL.
- Move page-level attributes into `event_params` instead of one-off custom
  events that duplicate `page_view`.

**Remove**:

- Custom events that duplicate a recommended GA4 event (detected by identical
  `page_location` + `event_name` mapping).
- Zero-param events with no downstream consumer (dead weight).
- Debug/bucket leftovers: events with a single value or absurd cardinality.

## Data gaps & suggested improvements

- **Identity**: `user_pseudo_id` is cookie-scoped — cross-device funnels need a
  `user_id` param (or Google Signals). State clearly what the funnel counts
  (sessions, not people) when user_id is absent.
- **`items` array**: if empty or missing `item_category`/`price`, product-level
  funnel and revenue attribution are impossible. Suggested: enforce the items
  schema on view_item/add_to_cart/purchase.
- **Sessionization**: high single-event-session share or absurd `max_events`
  means the session config is broken — check `session_start`/`session_end`
  coverage and `ga_session_id` reset rules (30-min timeout, campaign change).
- **Sequence integrity**: missing `event_bundle_sequence_id` weakens the
  dedupe key; enable event bundling in the export.
- **Suggested fixes**: implement recommended events in GTM/GA4, standardize
  parameter naming, enable `user_id` + Google Signals, add server-side events
  for refunds/back-office data, and set up ecommerce parameter validation.

## Querying from PowerShell (Windows)

`bq.cmd` mangles quotes and backticks. Reliable pattern:

```powershell
$env:Path = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin;" + $env:Path
& "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\bq.cmd" query '--project_id=PROJECT' '--use_legacy_sql=false' '--format=pretty' "SELECT event_name FROM ``{project_id}.{dataset}.events_*`` WHERE _TABLE_SUFFIX BETWEEN '20260701' AND '20261001'"
```

- PowerShell **double-quoted** string + **doubled backticks** (``) for BigQuery identifiers.
- SQL string literals with **single quotes** (`'20260701'`); double quotes get stripped by the cmd wrapper.
- Dry run first: add `'--dry_run'` to see bytes scanned, then add `--maximum_bytes_billed=500000000` to the real run.
- Always pass `--project_id` (no default project is set).
