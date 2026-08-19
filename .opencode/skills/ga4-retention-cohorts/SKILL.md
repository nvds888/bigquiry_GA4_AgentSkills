---
name: ga4-retention-cohorts
description: Analyze user retention and the SaaS growth loop from a GA4 raw BigQuery export — weekly new-user cohorts and their retention curves, activation rate, trial-to-paid conversion (median days), and churn proxy. Fully data-driven: cohort and activation/subscription events are discovered from the export, never hardcoded. Use when the user asks about retention, cohort analysis, DAU/WAU/MAU trends by cohort, churn, trial-to-paid, time-to-value, or whether users come back.
---

# GA4 Retention Cohorts

Answer the growth-loop questions the funnel can't: do users come back (weekly
retention curves per cohort), do they activate, do trials turn into paid
subscriptions, and is churn being tracked at all.

> **Core principle — discovery, not spec.** Cohort definitions come from the
> export's columns (`user_first_touch_timestamp`, `event_date`); the
> activation, trial, subscribe, and cancel events are the **discovered** events
> mapped to those roles (reference §4a), never fixed names. Read
> **`../sql-templates.md`** (shared templates in the `skills/` folder) before
> querying — §1 (discovery), §4 (role vocabulary), §9 (retention templates),
> §5c (user-level funnel) are the source of truth.

## Parameters

- `{project_id}` — billing project / where the export lives
- `{dataset}` — e.g. `analytics_123456789`
- `{table}` — GA4 export prefix `events_*`; always filter with `_TABLE_SUFFIX`.
- `{start}` / `{end}` — the analysis window, YYYYMMDD. **Note:** for retention
  you typically need a *longer* window than one week (cohorts need ≥4 weeks of
  follow-up); pick `{start}` at least 4–6 weeks before `{end}`.
- `{property_type}` — optional hint (`ecommerce`/`saas`). Retention is most
  meaningful for SaaS; for ecommerce, use it for repeat-purchase behavior.
- `{cohort_granularity}` — `weekly` (default) or `daily`; weekly is standard
  for SaaS retention curves.

## Cost-safety (always, before any query)

Same rules as `ga4-events`: `_TABLE_SUFFIX` filter, dry-run first,
`--maximum_bytes_billed=2000000000` (2 GB) on the real run, `SELECT` only
needed columns, single-quoted SQL string literals, doubled backticks for
identifiers, always `--project_id`. Retention queries scan the whole window
twice (cohort + activity) — the cap matters.

## Workflow

1. **Cost-safe setup**: dry-run; set the cap.
2. **Discover** (reference §1):
   - confirm columns: `user_pseudo_id`, `event_date`, `event_timestamp`,
     `user_first_touch_timestamp`, `user_id`;
   - event inventory + role mapping (reference §1a, §4a) to find the
     activation, trial, subscribe/purchase, and cancel events — the ones that
     exist, and the roles that are missing (missing roles become *suggested
     additions*, not assumptions);
   - quality gate (reference §2) — record PASS/FAIL and state it.
3. **Cohorts + retention curves** (reference §9a): cohort = week of
   `user_first_touch_timestamp`; activity = any event that week. Report
   `cohort_size` and `wk0..wk4 retention %`. Keep cohorts with < ~100 users
   flagged as noise (reference §9a note).
4. **Activation** (reference §9b): activation rate = users who reach the
   discovered activation-role event ÷ signup users. If no activation-role event
   exists, *suggest* it (it's the single most valuable SaaS event to add) and
   skip the metric rather than inventing one.
5. **Trial-to-paid** (reference §9b/§5c): median days between the discovered
   trial event and subscribe/purchase event; per-cohort if sample allows.
   For SaaS, note that MRR/frequency belongs to Stripe, not GA4.
6. **Churn proxy** (reference §9c): users with the discovered cancel event ÷
   users. If no cancel event exists, report churn as **untracked** and suggest
   `cancel_subscription` (with `plan` + `reason`).
7. **Interpret**:
   - a healthy SaaS curve stabilizes above ~30–40% after wk1–wk2; a monotone
     slide to near-0 means the product doesn't retain;
   - compare cohorts to each other — a *later* cohort retaining better than
     earlier ones means onboarding improvements are working;
   - activation rate is the leading indicator of retention: cohorts with
     higher activation should retain better (if they don't, the activation
     event may be firing on the wrong action — verify its `page_location`).
8. **Report** in the standardized order below.

## Standardized output

1. `Quality gate` — PASS/FAIL + dup rate + null-key shares.
2. `Discovery` — cohort-relevant events found (activation/trial/subscribe/
   cancel) + property-type inference + which roles are missing.
3. `Cohort retention table` — `cohort week | size | wk0..wk4 retention %`.
4. `Retention curve` — 2-3 cohorts plotted row-wise (or a textual summary for
   small data), plus cohort-over-cohort comparison.
5. `Activation & trial-to-paid` — activation rate, median trial→paid days
   (with the discovered events used).
6. `Churn` — churn proxy or "untracked, suggest adding".
7. `Insights & recommendations` — what the curves mean + suggested tracking
   additions tied to observed gaps.

## Caveats to always state

- **Cookie-based identity**: without `user_id`, cohorts fragment across
  devices/browser clears — retention is understated. For SaaS, empty `user_id`
  is a defect, note it.
- **First-touch attribution**: cohorts key on `user_first_touch_timestamp`;
  if it's not populated, fall back to `MIN(event_date)` per user and label the
  approximation.
- **Window length**: short windows truncate the tail of young cohorts — only
  report wkN for cohorts that had N weeks of follow-up within the window.
- **Activity definition**: "active" = ≥1 event that week; if the product
  defines activity more strictly, say so.

## Querying from PowerShell (Windows)

Use the `bq.cmd` pattern from `ga4-events`: double-quoted SQL, doubled
backticks for identifiers, single-quoted string literals, always
`--project_id=<PROJECT>`.