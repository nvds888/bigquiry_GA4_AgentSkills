# Analytics Skills Overview

This workspace contains a set of **opencode skills** for analyzing event /
web-analytics data in BigQuery. They are **agnostic to the dataset** — you can
point them at any BigQuery export (GA4 raw export or a flat events table) by
supplying a few parameters.

## Design principle: discovery, not spec

**No event name is hardcoded anywhere in the skills.** Every skill discovers
what actually exists in the export — the event inventory, each event's
parameters, and which pages events fire on — and builds its analysis from what
it finds. The shared *role vocabulary* (`skills/sql-templates.md`
§4) is a heuristic prior used to label observed events and to *suggest* what's
missing; it is never treated as a list of events that must exist.

Consequences, applied consistently across all six skills:

- Funnel steps are the **discovered** events, reported as `step → event` before
  any rate is shown.
- Missing recommended events are **suggested additions** tied to the funnel
  stage they unblock — not hardcoded expectations.
- Param coverage is computed from the **discovered parameter map**, not a fixed
  event→param table.
- `{property_type}` (`ecommerce`/`saas`) is an **optional hint**; if omitted it
  is inferred from the inventory and the inference is reported.
- Recommendations are always phrased as suggestions tied to observed gaps.

## The skills (agents)

| Skill | What it does | Best for | Key output |
| --- | --- | --- | --- |
| `ga4-tracking-audit` | Checks whether the data is **trustworthy** before any analysis: per-event parameter coverage, missing recommended events (as suggestions), duplicate/orphan events, session anomalies | any `events_*` export | Tracking health score + prioritized fix list (GTM vs code vs schema) |
| `ga4-tracking-trend` | Monitors tracking health **over time** — quality gate + health score per week/month to catch silent regressions | any `events_*` export, ≥6 weeks of data | Trend table + flagged regression buckets |
| `ga4-events` | Discovers the inventory, classifies each event as indicative vs noise, builds the **funnel from discovered events**, recommends events to add/fix/remove | any `events_*` export | Funnel + event recommendations + data gaps |
| `ga4-segmentation` | Runs the funnel **per segment** (traffic source, device, geo, new vs returning) to find over-/under-performers | any `events_*` export (depends on `ga4-events`) | Segment x funnel tables + best/worst segments |
| `ga4-retention-cohorts` | User-level growth loop: weekly **cohort retention curves**, activation rate, trial-to-paid, churn proxy | SaaS exports, ≥4–6 weeks of data | Retention table + activation/churn metrics |
| `ga4-kpi-snapshot` | **Briefing snapshot**: DAU/WAU/MAU, stickiness, sessions, engagement, new vs returning, top pages/events, WoW/MoM deltas | "last week / last month" overviews | One-page KPI briefing + deltas |

All analytics skills share a **standardized report format**:

1. `Quality gate` — PASS/FAIL per data-quality check
2. `Discovery` — event inventory + property-type inference + event→role mapping
3. `Event inventory & classification` / funnel
4. `Session funnel` — step rates + biggest leak
5. `Recommendations` — ADD / FIX / REMOVE, each tied to a gap (always suggested)
6. `Data gaps & suggested improvements`

## Which skill for which data shape

| Your table looks like | Use |
| --- | --- |
| GA4 raw export (`event_name`, `event_params`, `items`, `traffic_source`, `device`, `geo`) | `ga4-tracking-audit` → `ga4-tracking-trend` → `ga4-events` → `ga4-segmentation` → `ga4-retention-cohorts` → `ga4-kpi-snapshot` |
| Flat events table (`event_type`, `session_id`, `sequence_number`, `uri`) | `ga4-events` adapted (discovery queries use the event/param columns that exist) |
| Anything else / unknown schema | Start with `ga4-tracking-audit`; run the schema check first |

The skills work on **ecommerce or SaaS** GA4 exports. `{property_type}` is
inferred from the inventory (SaaS: signup/login/trial/subscribe/cancel roles;
ecommerce: product/cart/checkout roles) and can be overridden per run. All
branches share the same discovery, quality-gate, and coverage machinery — only
the role vocabulary expectations differ, and even those adapt to the events
that actually exist.

**Recommended pipeline (always):** audit the data's trustworthiness *before*
reporting funnels — every defect in the data silently corrupts the funnel and
segment numbers.

## Getting started fresh (any dataset)

### 1. Prerequisites

- `gcloud` CLI installed (the skills assume the standard Windows path
  `%LOCALAPPDATA%\Google\Cloud SDK\google-cloud-sdk\bin`).
- Authenticated: `gcloud auth login` (check with `gcloud auth list`).
- A **billing project id** for `bq` queries (BigQuery requires a project for
  billing even when querying public data):
  ```powershell
  gcloud config set project <PROJECT_ID>
  ```
- Access to the target dataset (any GCP project with BigQuery enabled).

### 2. Find the data

```powershell
bq ls <project_id>                       # list datasets
bq ls <project_id>:<dataset>             # list tables
bq ls bigquery-public-data              # e.g. public datasets
```

### 3. Check the schema

Each skill starts by verifying columns exist before querying:

```sql
SELECT column_name, data_type
FROM `<project>.<dataset>.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name = '<table>'
ORDER BY ordinal_position
```

### 4. Querying from PowerShell (Windows)

`bq.cmd` mangles quotes and backticks. Reliable pattern:

```powershell
$env:Path = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin;" + $env:Path
& "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\bq.cmd" query `
  '--project_id=<PROJECT_ID>' '--use_legacy_sql=false' '--format=pretty' `
  "SELECT ... FROM ``<project>.<dataset>.<table>`` ..."
```

- Wrap SQL in a PowerShell **double-quoted** string; use **doubled backticks**
  (``) for BigQuery backtick identifiers.
- Use SQL string literals with **single quotes** (`'20260701'`) — double quotes
  get stripped by the cmd wrapper.
- Always pass `--project_id=<PROJECT_ID>` (no default project is set).
- GA4 exports are date-partitioned: filter with `_TABLE_SUFFIX BETWEEN ...`
  and dry-run first (`--dry_run`) + set `--maximum_bytes_billed` to cap costs.

### 5. Parameters each skill asks for

| Skill | Parameters |
| --- | --- |
| `ga4-tracking-audit` / `ga4-events` | `{project_id}`, `{dataset}`, `{start}`/`{end}` (YYYYMMDD), `{property_type}` (optional, inferred) |
| `ga4-tracking-trend` | same + `{bucket}` (weekly/monthly); recommend ≥6 weeks |
| `ga4-segmentation` | same + `{segment_dim}` (source / device / geo / new vs returning) |
| `ga4-retention-cohorts` | same + `{cohort_granularity}` (weekly/daily); recommend ≥4–6 weeks |
| `ga4-kpi-snapshot` | same + `{period}` (weekly/monthly) |

### 6. Known data quirks to watch for (learned from real runs)

- **`user_id` missing ≠ clean data.** In many exports it's empty (cookies
  only); in flat lookup-style tables it can exist *only on buyers* — per-user
  funnels are meaningless in both cases. Always check. For **SaaS**, an empty
  `user_id` is a tracking defect, not a neutral note.
- **Zero-param counts hide partial tracking.** Events can fire but carry no
  `items`/`value`/`currency` (e.g. a checkout step with 0% items). The
  coverage assessment (from the parameter map) catches this.
- **`value` can be any numeric param type.** Check `int_value` +
  `float_value` + `double_value` (and the `event_value_in_usd` column in newer
  exports) — reading only one type under-reports purchase value by 30%+.
- **`ecommerce.transaction_id` can be a constant placeholder.** On
  `ga4_obfuscated_sample_ecommerce` it's the *same value on every event*
  (cardinality 1) except on `purchase`. The §6a hygiene check catches this —
  never count it as real transaction coverage.
- **Attribution artifacts:** `(data deleted)`, `(not set)`, `<Other>` sources
  and self-referral domains show inflated conversion — flag as noise, not wins.
- **Flat segments are a finding.** If all segments deviate < ~2pp from
  baseline, the problem is site-wide, not segment-specific.
- **Synthetic/template data** can have rigid session shapes and inflated
  `items`-array lengths (e.g. `add_to_cart` avg 11 items on the sample) —
  verify session patterns and item cardinality before trusting "behavioral"
  conclusions.
- **High step-through is a red flag.** A mid-funnel step retaining >90% of the
  previous step usually fires on page load, not on the user action — check the
  event's trigger before reporting it as a conversion step.
- **SaaS conversions span sessions.** A session funnel under-reports SaaS
  (signup → trial → subscribe takes days). Use the user-level funnel for SaaS
  and report median trial→paid days.
- **SaaS revenue lives in Stripe, not GA4.** GA4 `value` on `subscribe` is a
  first-payment signal only; MRR/churn/frequency belong to the billing system.
  Report GA4 revenue gaps as coverage defects, but don't treat missing MRR in
  GA4 as a tracking failure.

## When the Gastronomixs data becomes available

1. Confirm the GCP project + dataset + schema (step 1–3 above).
2. Run `ga4-tracking-audit` → establishes the **tracking health score** and a
   prioritized fix list (GTM vs code).
3. Run `ga4-tracking-trend` once enough weeks of data accumulate → catches
   silent regressions early.
4. Run `ga4-events` → funnel + which events to add/fix/remove (all suggested).
5. Run `ga4-segmentation` → which segments (traffic source / device / geo /
   new vs returning) convert best and where they leak.
6. Run `ga4-retention-cohorts` → do users come back, activate, and pay.
7. Run `ga4-kpi-snapshot` weekly/monthly → the standing briefing.
8. Every report ends with concrete, actionable recommendations tied to
   observed gaps — ready to hand to the tracking owner.