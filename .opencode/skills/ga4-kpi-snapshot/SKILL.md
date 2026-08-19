---
name: ga4-kpi-snapshot
description: Produce a briefings-style KPI snapshot from a GA4 raw BigQuery export — DAU, WAU, MAU, stickiness (DAU/MAU), sessions, engagement rate, new vs returning users, top pages/events, and WoW or MoM deltas for last week/month. Fully data-driven: it discovers what's in the export (users, sessions, engagement events) rather than assuming a fixed event set. Use when the user wants an overview of last week's or last month's data, a weekly/monthly briefing, a KPI overview, or DAU/MAU/stickiness numbers.
---

# GA4 KPI Snapshot

Produce a briefing-ready snapshot of the last week's (or month's) data:
DAU, WAU, MAU, stickiness, sessions, engagement, new vs returning users, top
pages/events, and deltas vs the previous period — without the depth of a full
funnel or tracking audit.

> **Core principle — discovery, not spec.** No event name is assumed. The
> snapshot computes metrics from the export's actual columns
> (`user_pseudo_id`, `event_date`, `ga_session_id`) and uses only *discovered*
> events (e.g. the engagement event, the conversion event) for the parts that
> need one. Read **`../sql-templates.md`** (shared templates in the `skills/`
> folder) before querying — sections §1 (discovery), §8 (KPI templates),
> §5b/5c (funnel summary) are the source of truth.

## Parameters

- `{project_id}` — billing project / where the export lives
- `{dataset}` — e.g. `analytics_123456789`
- `{table}` — GA4 export prefix `events_*`; always filter with `_TABLE_SUFFIX`.
- `{start}` / `{end}` — the period to snapshot, YYYYMMDD (e.g. last 7 or 30 days)
- `{period}` — `weekly` (default) or `monthly`; sets the comparison window
  (previous equal-length period) and the WAU/MAU lookback used
- `{property_type}` — optional hint (`ecommerce`/`saas`), inferred if omitted

## Cost-safety (always, before any query)

Same rules as `ga4-events`: `_TABLE_SUFFIX` filter, dry-run first,
`--maximum_bytes_billed=2000000000` (2 GB) on the real run, `SELECT` only
needed columns, single-quoted SQL string literals, doubled backticks for
identifiers, always `--project_id`.

## Workflow

1. **Cost-safe setup**: dry-run the first query; set the cap.
2. **Discover** (reference §1):
   - confirm columns exist (reference §1d): `user_pseudo_id`, `event_date`,
     `event_timestamp`, `user_first_touch_timestamp`, and whether `session_id`
     is a column or the `ga_session_id` param;
   - event inventory (reference §1a) to find the engagement event
     (`user_engagement` role) and the conversion event (`purchase`/`subscribe`
     role) — these are used where a named event is needed;
   - run the quality gate (reference §2) briefly and note PASS/FAIL (a FAIL
     means the snapshot's numbers are suspect — say so, don't hide it).
3. **Core metrics** (reference §8):
   - §8a DAU + sessions + engaged sessions per day (and engagement rate);
   - §8b WAU / MAU / stickiness (WAU/MAU and avg DAU/MAU) as of `{end}`;
   - §8c new vs returning sessions (cookie-based; state that when `user_id` is
     empty — for SaaS an empty `user_id` is a defect, note it as such).
4. **Macro funnel summary** (reference §5b or §5c): compact step table with the
   discovered step events; for SaaS use the user-level funnel (§5c). Show the
   conversion step (purchase/subscribe role) rate as the headline.
5. **Top pages & top events**: top `page_location` values (reference §1c) and
   top events from the inventory (exclude the obvious markers if you like, but
   say what you excluded).
6. **WoW / MoM deltas** (reference §8d): re-run the same core metrics on the
   previous equal-length window (`{start}`/`{end}` shifted back by the period
   length) and report % change for avg DAU, WAU/MAU, engagement rate, sessions,
   and the macro conversion rate. Always label the comparison window.
7. **Report** the snapshot in the standardized order below. Keep it briefing-
   length: tables and % changes, one line of implication per number, no essay.

## Standardized output

1. `Quality gate` — one-line PASS/FAIL (dup rate, null keys, session integrity).
2. `Headline KPIs` — table: `metric | value | vs previous period`.
3. `Daily series` — DAU + sessions + engagement rate per day (compact).
4. `Acquisition` — new vs returning sessions, top pages, top events.
5. `Macro funnel` — discovered-step funnel + conversion rate.
6. `Notable changes & caveats` — deltas that moved >10%, plus data caveats
   (cookie-based identity, sample size, quality-gate notes).

## Caveats to always state

- **Cookie-based users**: without `user_id`, DAU/WAU/MAU count cookies, not
  people. For SaaS, missing `user_id` is a tracking defect worth calling out.
- **Engagement definition**: engagement rate uses the *discovered* engagement
  event (`user_engagement` role); if the export uses a different name, the
  snapshot uses that event and says so.
- **WAU/MAU lookback**: WAU = active in last 7 days, MAU = last 30 days as of
  `{end}`. For a monthly snapshot, state that MAU ≈ the window itself.
- **Quality gate failure**: if dup rate > 0.5% or session keys are broken, the
  headline numbers are inflated — lead with that, then give best-effort
  numbers, or dedupe (reference §3) if cheap.

## Querying from PowerShell (Windows)

Use the `bq.cmd` pattern from `ga4-events`: double-quoted SQL, doubled
backticks for identifiers, single-quoted string literals, always
`--project_id=<PROJECT>`.