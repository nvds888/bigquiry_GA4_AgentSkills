---
name: ga4-tracking-audit
description: Audit the tracking completeness and correctness of a GA4 raw BigQuery export — tracking health score, event x required-parameter coverage matrix, items-array integrity, missing recommended events, duplicate/renamed events, session anomalies, and a prioritized fix list split by GTM vs app code. Use when the user asks whether their tracking is trustworthy, what tracking is broken or missing, data completeness, what events lack required parameters, what to fix in GTM vs in code, or a tracking health check.
---

# GA4 Tracking Audit

Answer one question: **can this GA4 export be trusted for analytics?** Checks
which recommended events are missing, which fire but are incomplete (no
`items`, no `value`, no `transaction_id`), which are duplicated, and what to
fix — and routes each fix to GTM or app code.

Complements `ga4-events` (classification + funnel) and `ga4-segmentation`
(segment comparison). **Run this first**: both skills inherit your data's
defects, so audit before you report funnels.

Depends on `ga4-events` for the schema reference and cost-safety rules. Load
it first and reuse its templates.

## Parameters

Same as `ga4-events`: `{project_id}`, `{dataset}`, `{start}`, `{end}`.

## Workflow

1. **Cost-safe setup**: date-window filter on `_TABLE_SUFFIX`, dry-run,
   `--maximum_bytes_billed`. Same rules as `ga4-events`.
2. **Quality gate**: run the dup / null-key / session-integrity checks from
   `ga4-events`. Record PASS/FAIL. If dup rate > 0.5%, run every query below
   against the `events_dedup` CTE.
3. **Recommended-event presence**: expected GA4 recommended events vs the
   inventory. Each missing event hides a funnel stage. Standard ecommerce set:
   `session_start`, `first_visit`, `view_item_list`, `view_item`,
   `select_item`, `add_to_cart`, `remove_from_cart`, `view_cart`,
   `begin_checkout`, `add_shipping_info`, `add_payment_info`, `purchase`,
   `refund`, `search`, `select_promotion`, `view_promotion`.
4. **Required-param coverage matrix** (SQL below): which events fire but lack
   the params they must carry. Report coverage % per event.
5. **`items`-array integrity** (SQL below): coverage per commerce step, plus
   list-impression vs click consistency (`view_item_list` vs `select_item`).
6. **Duplicate / renamed events**: same semantic event under multiple names
   (e.g. `addtocart` + `add_to_cart`), custom events duplicating recommended
   ones (same `page_location`), events that look like debug/bucket leftovers.
7. **Session anomalies**: single-event share, sessions > 100 events, missing
   `session_start`, broken `ga_session_id` (see `ga4-events` quality gate).
8. **Score + fix list** (see Scoring and Fix routing below).

## Scoring (tracking health)

Health score = `100 * (params present / params expected)` summed over the
expected events for the property type (web ecommerce vs app vs SaaS). Report:

- overall score;
- per-event coverage table (`event | expected params | % complete | defect`);
- anything < 100% on a commerce event is a **defect**, not a behavior — it
  silently truncates funnels and revenue attribution.

## SQL: required-param coverage matrix

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

## SQL: items-array integrity

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

List-impression consistency:

```sql
SELECT
  SUM(IF(event_name = 'view_item_list', 1, 0)) AS list_impressions,
  SUM(IF(event_name = 'select_item', 1, 0)) AS item_clicks,
  SUM(IF(event_name = 'view_item', 1, 0)) AS item_views
FROM `{project_id}.{dataset}.events_*`
WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
```

Rule: impressions ≥ clicks ≥ views. `view_item_list` near zero while
`select_item`/`view_item` are large → list impressions are untracked (blind
category/list analysis).

## Fix routing (GTM vs app code)

Route each fix; never say "track it better" without saying where:

- **GTM only** (no app release):
  - forward the existing `ecommerce.items` dataLayer object to steps that
    omit it (`add_payment_info`, `begin_checkout`) — most common items defect;
  - canonicalize/merge duplicate event names; add/change GA4 tag triggers;
  - events driven by page load / URL / DOM (e.g. `view_item_list` on category
    pages via a page-view trigger reading visible items, `search` from the URL
    `?q=` param);
  - enrich params readable from DOM/URL (e.g. `item_list_name` from breadcrumb
    or page title).
- **App code**:
  - events whose data exists only in JS state or backend: cart contents,
    `order_id`/`transaction_id`, `value`/`currency`, payment status, `user_id`;
  - `purchase`/`refund` (and payment failures) — recommend server-side
    (Measurement Protocol / server-side GTM) so the client can't be trusted
    for order totals;
  - pushing `ecommerce.items` in the first place if the dataLayer never
    receives it.
- **Cannot fix in tracking**: defects that need schema/config changes
  (`user_id` + Google Signals for cross-device identity, enabling
  `event_bundle_sequence_id`, session rules).

## Standardized output

1. `Quality gate` — PASS/FAIL + dup rate + null-key shares.
2. `Tracking health score` — overall score + per-event coverage table.
3. `Missing recommended events` — each mapped to the funnel stage it hides.
4. `Duplicate / orphan / low-value events` — list + why each is noise.
5. `Session anomalies` — with evidence.
6. `Prioritized fix list` — each item tagged GTM / code / schema, with what
   the fix unlocks (e.g. "forward items to add_payment_info (GTM) → restores
   product-level revenue at the final checkout step").

## Querying from PowerShell (Windows)

Use the `bq.cmd` pattern from `ga4-events`: double-quoted SQL, doubled
backticks for identifiers, single-quoted string literals, always
`--project_id=<PROJECT>`.
