---
name: ga4-events
description: Analyze GA4 raw BigQuery export events — discover the actual event inventory, classify each observed event as indicative vs noise, build the conversion funnel from the events that really exist, and recommend what to add/fix/remove. Fully data-driven: no event names are hardcoded, the skill adapts to whatever events (SaaS, ecommerce, or anything else) the export contains. Use when the user asks about GA4 event inventory, event classification, funnel analysis on GA4 data, which events matter, what events to track, conversion funnel, or data gaps.
---

# GA4 Events Analysis

Analyze the raw GA4 event export in BigQuery and classify each **observed**
`event_name` as indicative/useful or noise, build a conversion funnel from the
events that actually exist, and audit event hygiene.

> **Core principle — discovery, not spec.** This skill never assumes an event
> exists. It discovers the inventory, maps events to *roles* using a heuristic
> vocabulary, builds the funnel only from discovered events, and *suggests*
> additions for the roles that are missing. Read **`../sql-templates.md`**
> (the shared templates file in the `skills/` folder) before querying — it is the single source of truth
> for the discovery queries, quality-gate, funnel, and coverage SQL plus the
> thresholds, shared with `ga4-tracking-audit`, `ga4-segmentation`,
> `ga4-kpi-snapshot`, `ga4-retention-cohorts`, and `ga4-tracking-trend`. Edit
> templates there, never inline in a SKILL.md.

## Parameters (substitute per run)

- `{project_id}` — billing project / where the export lives
- `{dataset}` — e.g. `analytics_123456789`
- `{table}` — GA4 export prefix: `events_*` (covers `events_YYYYMMDD` and `events_intraday_*`). Always filter with `_TABLE_SUFFIX BETWEEN '{start}' AND '{end}'`.
- `{start}` / `{end}` — date window in YYYYMMDD, e.g. `'20260701'` .. `'20261001'`
- `{property_type}` — **optional** hint (`ecommerce` or `saas`). If omitted,
  inferred from the inventory (see reference §4b). The inference is reported;
  the hint only selects which vocabulary roles are *expected*.

## Reference schema (raw GA4 export, web)

| column | notes |
| --- | --- |
| `event_name` | STRING, the event |
| `_TABLE_SUFFIX` / `event_date` | YYYYMMDD; the table is date-partitioned |
| `event_timestamp` | INTEGER microseconds |
| `event_params` | ARRAY<STRUCT<key, value>>; extract with `(SELECT value.int_value|string_value FROM UNNEST(event_params) WHERE key = '...')` |
| `user_pseudo_id` | STRING, cookie/app id |
| `session_id` | STRING in newer exports; otherwise the `ga_session_id` event param |
| `items` | ARRAY<STRUCT<item_id, item_name, item_category, price, quantity, ...>> |
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
- Run a **dry run** first to check bytes scanned before paying for the real run (see PowerShell pattern below). **Caveat:** a dry-run against a `events_*` wildcard can report `totalBytesProcessed: 0` (misleading) — the real run will scan the data, so still set a cap.
- Set `--maximum_bytes_billed` so a runaway query fails instead of billing. Start at `--maximum_bytes_billed=2000000000` (2 GB) and tighten after seeing the first real-run byte count.
- `SELECT` only needed columns; avoid `SELECT *` on wide exports.
- Repeated identical queries hit the free 24h cache.

## Workflow

1. **Cost-safe setup**: pick the date window; dry-run the first query; set
   `--maximum_bytes_billed`.
2. **Discover** (reference §1) — everything downstream derives from these:
   - §1a **event inventory with evidence** (count, users, distinct pages,
     zero-param count, session coverage);
   - §1b **parameter map per event** (what params each event actually carries);
   - §1c **page map per event** (what each event fires on — used to detect
     duplicates/misconfigs);
   - §1d **session-key check** (`session_id` column vs `ga_session_id` param).
3. **Quality gate** (reference §2): run dup / null-key / session-integrity
   checks. Record PASS/FAIL. If dup rate > 0.5%, run the rest against the
   dedupe pattern (§3).
4. **Property-type inference** (reference §4b): infer `saas` vs `ecommerce`
   from the discovered roles; report the evidence. Use `{property_type}` only
   as an override.
5. **Role mapping** (reference §4a): label each non-noise observed event with
   its best-matching role, and report the `event → role → evidence` table.
   Classify unknown events by parameter evidence. If two names map to the same
   role (e.g. `sign_up` + `create_account`), note the duplicate.
6. **Session funnel** (reference §5b): substitute the **discovered** step event
   names into the funnel SQL. Keep `session_start` as step 1. If the export is
   SaaS-shaped (trial/subscribe), also run the **user-level funnel** (§5c) —
   SaaS conversions span sessions, so a session funnel alone under-reports.
7. **Hygiene audit**:
   - **parameter coverage** from the param map (reference §6): meaningful
     params per role, coverage < 95% = defect; 0% on a conversion event =
     structural;
   - **transaction-id placeholder check** (§6a) when subscribe/purchase roles
     exist;
   - zero-param events (dead weight / missing tracking);
   - duplicate/renamed events (same top `page_location` from §1c);
   - events with absurd cardinality or a single value (debug leftovers);
   - **missing roles** → these become *suggestions*, not measured steps.
8. **Classify + recommend**: cite observed counts; list what to keep / add /
   remove / rename, each tied to a funnel stage or coverage gap. Always report
   the quality-gate result alongside findings.

## Event classification rubric

**Indicative / useful** — any observed event whose role signals intent or
outcome (see reference §4a vocabulary): purchase/subscribe, checkout/payment,
cart actions, product views/selects, search (with `search_term`), promotions,
signup/login/lead, trial start, upgrade/downgrade, cancel, activation. **This
list is derived from the role mapping, not fixed** — if the data uses different
names that carry the same params, they classify as indicative.

**Noise / low value** (by evidence, not by name):
- `page_view`, `scroll`, generic `click`, `user_engagement` — volume with weak
  intent;
- `session_start`, `first_visit` — session markers only;
- any observed event with **no event_params** and no downstream consumer;
- custom events that duplicate another event on the same `page_location`.

**Context-dependent:**
- `page_view` + `page_location` is the signal when the question is about *which pages*;
- `traffic_source`, `geo`, `device` carry the signal when the question is about *who the user is*.

## Recommendations (always suggestions, tied to observed gaps)

**Add when missing** — only suggest events that map to a **missing role** in
the discovered funnel (reference §4a). Every suggestion must name the observed
gap it fixes:

- Checkout micro-steps when the cart→purchase leak is the biggest:
  `begin_checkout`, `add_shipping_info`, `add_payment_info`, `purchase_error`.
- SaaS macro funnel gaps: `sign_up`, `pricing_view`, `start_trial`,
  `subscribe`, `tutorial_complete` (activation), `cancel_subscription` (churn),
  `upgrade`/`downgrade`.
- Intent events: `search` (with `search_term`), `view_item_list` with
  impressions, promotion events.
- Params to add on existing events: `currency`/`value` on revenue events,
  `plan`/`transaction_id`, `method` on signup/login, `search_term` on search —
  each tied to a coverage defect found in §6.

**Fix**: canonicalize renamed/duplicate events (same page_location, two names);
add missing `currency`/`value`/`items`; move page-level attributes into
`event_params`.

**Remove**: zero-param events with no consumer; debug/bucket leftovers (single
value or absurd cardinality); custom events duplicating a recommended event.

## Data gaps & suggested improvements

- **Identity**: `user_pseudo_id` is cookie-scoped — cross-device funnels need a
  `user_id` param (or Google Signals). State clearly what the funnel counts
  (sessions vs users) when `user_id` is absent. For `saas`, an empty `user_id`
  is a tracking defect, not a neutral note.
- **`items` array**: if empty or missing `item_category`/`price`, product-level
  funnel and revenue attribution are impossible.
- **Sessionization**: high single-event-session share or absurd `max_events`
  means the session config is broken.
- **Sequence integrity**: missing `event_bundle_sequence_id` weakens the
  dedupe key.
- **SaaS revenue**: GA4 `value` on `subscribe` is a first-payment signal only;
  MRR/churn lives in Stripe. State this when reporting SaaS revenue gaps.
- **Suggested fixes**: implement recommended events in GTM/GA4, standardize
  parameter naming, enable `user_id` + Google Signals, add server-side events
  for refunds/back-office data.

## Standardized output

Always produce the report in this fixed order with these headers:

1. `Quality gate` — PASS/FAIL per check + dup rate + null-key shares + session-integrity notes.
2. `Discovery` — event inventory summary + property-type inference + `event → role → evidence` mapping.
3. `Event inventory & classification` — table of `event | count | distinct users | distinct pages | role | classification | gap notes`.
4. `Session funnel` — `step | event used | sessions | % of sessions | step-to-step drop-off`; flag the single biggest leak. Add the user-level funnel for SaaS.
5. `Coverage` — per-event param coverage table + tracking health score.
6. `Recommendations (events)` — ADD / FIX / REMOVE, each tied to a gap or missing funnel role.
7. `Data gaps & suggested improvements` — tracking holes + concrete fixes.

## Querying from PowerShell (Windows)

`bq.cmd` mangles quotes and backticks. Reliable pattern:

```powershell
$env:Path = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin;" + $env:Path
& "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\bq.cmd" query '--project_id=PROJECT' '--use_legacy_sql=false' '--format=pretty' "SELECT event_name FROM ``{project_id}.{dataset}.events_*`` WHERE _TABLE_SUFFIX BETWEEN '20260701' AND '20261001'"
```

- PowerShell **double-quoted** string + **doubled backticks** (``) for BigQuery identifiers.
- SQL string literals with **single quotes** (`'20260701'`); double quotes get stripped by the cmd wrapper.
- Dry run first (may report 0 on a wildcard — still set a cap), then add `--maximum_bytes_billed=2000000000` (2 GB) to the real run.
- Always pass `--project_id` (no default project is set).