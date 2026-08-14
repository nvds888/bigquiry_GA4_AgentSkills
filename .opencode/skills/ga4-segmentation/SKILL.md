---
name: ga4-segmentation
description: Segment GA4 raw BigQuery export data by traffic source, device, geo, browser, platform, or new/returning users and run the funnel and event analytics per segment to find patterns and differences. Produces a standardized report (quality gate, baseline funnel, segment x funnel tables, event recommendations for segmentation, data gaps and suggested improvements). Use when the user asks for segmentation, segment comparison, segment funnel, which segments perform or convert better, or segment-level data gaps.
---

# GA4 Segmentation Analysis

Run the same funnel and event analytics as `ga4-events`, but sliced by
segment dimensions, to find which segments over-/under-perform and where
each segment leaks in the funnel.

Depends on the `ga4-events` skill for the reference schema, funnel
definition, event classification, and cost-safety rules. Load `ga4-events`
first and reuse its templates and event inventory.

## Parameters

Same as `ga4-events`: `{project_id}`, `{dataset}`, `{table}`, `{start}`,
`{end}`, `{property_type}` (ecommerce/saas). Plus `{segment_dim}` — one of the
segment columns below.

## Segment dimensions (default GA4 export)

| segment | column path |
| --- | --- |
| traffic source | `traffic_source.source`, `traffic_source.medium`, `traffic_source.name` (newer exports: `collected_traffic_source.source` / `.medium`) |
| device | `device.category` (mobile/tablet/desktop), `device.web_info.browser`, `device.operating_system` |
| geo | `geo.country`, `geo.region`, `geo.city` |
| platform | `platform` |
| user recency | `event_timestamp - user_first_touch_timestamp` → new (e.g. < 30 min since first touch) vs returning |
| marketing channel | (derived) classify from source/medium: Paid Search, Organic Search, Email, Direct, Referral, Paid Social |

Confirm the segment column exists in the schema (see `ga4-events` step for
checking `INFORMATION_SCHEMA.COLUMNS`) before querying. **Also confirm it is
populated on the events you segment with** — on some exports
`traffic_source`/`device`/`geo` are NULL on a subset of rows (older exports),
which silently drops sessions from the segment funnel. Run a quick NULL share
per segment column and report it; if a segment column is only populated on one
event type (e.g. `traffic_source` only on `session_start`), say so instead of
segmenting on a column that's empty for most of the funnel.

### New vs returning users (concrete template)

Often the strongest segment — run it even when other dimensions are flat.
When `user_id` is absent this is *cookie-based* (a cleared cookie = new).

```sql
WITH per_session AS (
  SELECT
    IF((SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_number') = 1,
       'new', 'returning') AS segment,
    (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS session_id,
    MAX(IF(event_name = 'view_item', 1, 0)) AS s2,
    MAX(IF(event_name = 'add_to_cart', 1, 0)) AS s3,
    MAX(IF(event_name = 'begin_checkout', 1, 0)) AS s4,
    MAX(IF(event_name = 'purchase', 1, 0)) AS s6
  FROM `{project_id}.{dataset}.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
  GROUP BY segment, session_id
)
SELECT segment, COUNT(*) AS sessions,
  ROUND(100 * SUM(s2)/COUNT(*), 1) AS view_rate,
  ROUND(100 * SUM(s3)/COUNT(*), 1) AS atc_rate,
  ROUND(100 * SUM(s4)/COUNT(*), 1) AS checkout_rate,
  ROUND(100 * SUM(s6)/COUNT(*), 1) AS purchase_rate,
  ROUND(100 * SUM(s3)/SUM(s2), 1) AS vi_to_atc,
  ROUND(100 * SUM(s6)/SUM(s4), 1) AS ck_to_purchase
FROM per_session GROUP BY segment ORDER BY sessions DESC
```

Fallback if `ga_session_number` is absent: compare
`event_timestamp - user_first_touch_timestamp` (first session < 30 min since
first touch ≈ new) — label the result as approximate.

### Segment column population guardrail

Before trusting any segment comparison, check how populated the segment column
actually is — a `traffic_source`/`device`/`geo` column that is NULL on a large
share of rows will silently drop those sessions and skew every rate:

```sql
SELECT
  COUNT(*) AS events,
  COUNTIF(traffic_source.source IS NULL) AS null_source,
  COUNTIF(device.category IS NULL) AS null_device,
  COUNTIF(geo.country IS NULL) AS null_country,
  COUNTIF(platform IS NULL) AS null_platform
FROM `{project_id}.{dataset}.events_*`
WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
```

- If a segment column is NULL on > ~5% of rows, report it as a data gap, don't
  pretend the segment covers the whole funnel.
- A column that is only populated on a specific event (e.g. `traffic_source`
  filled only on `session_start`) still works for session-scoped segmentation
  if you take it via `ANY_VALUE`/`MAX` per session — but say so in the output.
- `(not set)`/`(unknown)`/empty values are a segment of their own, not missing
  data — report them explicitly, never drop them (dropping overstates rates).

## Workflow

1. **Quality gate**: run the duplicate / null-key / session-integrity checks from `ga4-events`. Record PASS/FAIL. If the dup rate > 0.5%, run every segment query in this skill against the `events_dedup` CTE (replace the `FROM events_*` clause with `FROM events_dedup`). Never compare segments on unclean data.
2. **Cost-safe setup**: date-window filter on `_TABLE_SUFFIX`, dry-run, `--maximum_bytes_billed`. Same rules as `ga4-events`.
3. **Baseline**: compute the unfiltered session funnel (from `ga4-events`) so every segment can be compared against it.
4. **Segment funnel** — one query per segment dimension, funnel per session.
   Ecommerce steps shown; for `{property_type} = saas` substitute the SaaS
   macro-funnel steps (`sign_up` → `pricing_view` → `start_trial` →
   `subscribe`) from `sql-templates.md` §8:
   ```sql
   WITH per_session AS (
     SELECT
       {segment_dim} AS segment,
       (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS session_id,
       MAX(IF(event_name = 'session_start', 1, 0)) AS s1,
       MAX(IF(event_name = 'view_item', 1, 0)) AS s2,
       MAX(IF(event_name = 'add_to_cart', 1, 0)) AS s3,
       MAX(IF(event_name = 'begin_checkout', 1, 0)) AS s4,
       MAX(IF(event_name = 'add_payment_info', 1, 0)) AS s5,
       MAX(IF(event_name = 'purchase', 1, 0)) AS s6
     FROM `{project_id}.{dataset}.events_*`
     WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
     GROUP BY segment, session_id
   )
   SELECT segment,
     COUNT(*) AS sessions,
     SAFE_DIVIDE(SUM(s2), COUNT(*)) AS view_item_rate,
     SAFE_DIVIDE(SUM(s3), COUNT(*)) AS add_to_cart_rate,
     SAFE_DIVIDE(SUM(s4), COUNT(*)) AS begin_checkout_rate,
     SAFE_DIVIDE(SUM(s5), COUNT(*)) AS add_payment_info_rate,
     SAFE_DIVIDE(SUM(s6), COUNT(*)) AS purchase_rate,
     SAFE_DIVIDE(SUM(s6), SUM(s4)) AS cart_to_purchase_rate
   FROM per_session
   GROUP BY segment
   ORDER BY COUNT(*) DESC
   ```
   Drop/rename funnel steps to match the events that actually exist (from the event inventory in `ga4-events`).
5. **Drop-off per segment**: for each segment compute step-to-step drop-off and compare against the baseline (e.g. segment is 15% worse than baseline at `add_to_cart`). Flag segments that leak at a *specific* step, not uniformly.
6. **Composition analysis** (who converts vs who doesn't):
   ```sql
   WITH t AS (
     SELECT {segment_dim} AS segment, event_name, user_pseudo_id
     FROM `{project_id}.{dataset}.events_*`
     WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
   )
   SELECT segment,
     COUNT(DISTINCT user_pseudo_id) AS users,
     COUNT(DISTINCT IF(event_name = 'purchase', user_pseudo_id, NULL)) AS purchasers,
     SAFE_DIVIDE(COUNT(DISTINCT IF(event_name = 'purchase', user_pseudo_id, NULL)),
                 COUNT(DISTINCT user_pseudo_id)) AS conv_rate
    FROM t GROUP BY segment ORDER BY users DESC
   ```
   Identity note: this counts cookies (`user_pseudo_id`), not people — state
   that whenever `user_id` is empty. For a lookup-style table where `user_id`
   only exists on buyers (e.g. thelook), user-level conversion is meaningless;
   fall back to session-level funnels only.
7. **Interpretation guardrails**:
   - small-segment caution: flag segments with fewer than ~100 sessions in the window — rates are noise;
   - segments correlate with intent (e.g. Paid Search usually higher intent than Social) — don't call one channel "better" without controlling for mix;
   - a leak at the same step everywhere is a site-wide problem, not a segment problem;
   - **attribution artifacts**: flag `(data deleted)`, `(not set)`, `<Other>`, and self-referral domains (e.g. `shop.ownstore.com`) — their inflated conversion is noise, not a channel win;
   - **flat segments**: if every segment deviates < ~2pp from baseline, state it explicitly and focus on the site-wide funnel instead;
   - findings suggest *where to look*, not proof — propose an experiment / A-B test for causation.
   - always report the quality-gate result alongside segment output (PASS/FAIL + dup rate), so readers know whether segment differences are real or noise from unclean data.

## Standardized output

Produce the report in this fixed order with these headers:

1. `Quality gate` — PASS/FAIL + dup rate + null-key shares (reuse `ga4-events`).
2. `Baseline funnel` — the unfiltered session funnel every segment is compared against.
3. `Segment x funnel` — one table per segment dimension: `segment | sessions | step rates | cart_to_purchase | deviation vs baseline`.
4. `Best & worst segments` — the 2-3 over- and under-performers and at which step (or state explicitly when segments are flat).
5. `Recommendations (events)` — tracking/event changes that would make segmentation stronger.
6. `Data gaps & suggested improvements` — schema gaps + concrete fixes.

Keep `## Output format` guidance below as the template for sections 3-4:

### Output format

For each segment dimension:
- a table of segment x funnel rates + deviation vs baseline;
- the 2-3 segments that over- and under-perform, and at which step;
- practical implication (e.g. "Email converts 2x baseline at checkout — test a checkout change first for Email users" or "Mobile leaks at begin_checkout — checkout UX on mobile is the priority");
- if every segment deviates < ~2pp from baseline, say so explicitly — flat data means site-wide problems, not segment problems.

## Event recommendations (to strengthen segmentation)

**Add** (tie each to a segment gap found above):

- `begin_checkout` / `add_payment_info` per segment — required to localize a
  checkout leak to a device/geo/channel (the biggest missing step when
  cart->purchase leaks).
- `user_id` param on all events — without it a returning user is a new
  `user_pseudo_id` on every device, so device segments look like different
  people (breaks new/returning and cross-device segments).
- `transaction_id` + `value` + `currency` on `purchase` per segment — revenue
  per segment is impossible without them.
- `promotion_id`/`creative_name` on promotion events — isolates campaign-level
  segment performance.
- `item_category` on all commerce events — enables segment x category analysis.
- For "new vs returning" segments, ensure `user_first_touch_timestamp` and
  `user_engagement` are populated; otherwise derive recency from the data
  you have and label it as an approximation.

**Fix**:

- Populate `traffic_source` on every row (older exports have it NULL on some
  events) or consistently use `collected_traffic_source` — segment attribution
  currently depends on which row you sample.
- Standardize `device.category` (mobile/tablet/desktop) and
  `device.web_info.browser`; "(not set)" values must be reported, not dropped.

## Data gaps & suggested improvements

- **Identity is cookie-scoped**: `user_pseudo_id` fragments cross-device users;
  device segments double-count the same person. Suggested: enable Google
  Signals or pass `user_id` on login.
- **Attribution is single-touch**: the segment is taken from the session-start
  event; conversions later in the session inherit that touch. Suggested: record
  `source`/`medium` on the converting event itself for last-click accuracy.
- **Missing session column**: if `session_id` is not a first-class column,
  `ga_session_id` param must be parsed per event — errors here silently break
  every segment funnel. Suggested: enable session_id columns in the export.
- **Small segments are noise**: below ~100 sessions in the window, rate
  differences are meaningless — always flag them, never rank them.
- **Blank/unknown segment values**: report `(not set)`/`unknown` shares
  explicitly; dropping them overstates rates for known segments.
- **Suggested fixes**: enable user_id + signals, add checkout micro-steps,
  populate traffic_source/device fields on all rows, and validate segment
  columns in `INFORMATION_SCHEMA` before each run.
