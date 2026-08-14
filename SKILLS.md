# Analytics Skills Overview

This workspace contains a set of **opencode skills** for analyzing event / web-analytics
data in BigQuery. They are **agnostic to the dataset** — you can point them at any
BigQuery export (GA4 raw export or a flat events table) by supplying a few parameters.

## The skills (agents)

| Skill | What it does | Best for | Key output |
| --- | --- | --- | --- |
| `ga4-tracking-audit` | Checks whether the data is **trustworthy** before any analysis: required-parameter coverage matrix, `items`-array integrity, missing recommended events, duplicate/orphan events, session anomalies | GA4 `events_*` exports | Tracking health score + prioritized fix list (GTM vs code vs schema) |
| `ga4-events` | Classifies each event as indicative vs noise, builds the **session funnel**, recommends events to add/fix/remove | GA4 `events_*` exports | Funnel + event recommendations + data gaps |
| `ga4-segmentation` | Runs the funnel **per segment** (traffic source, device, geo, new vs returning) to find over-/under-performers | GA4 `events_*` exports (depends on `ga4-events`) | Segment x funnel tables + best/worst segments |

All three share a **standardized report format**:

1. `Quality gate` — PASS/FAIL per data-quality check
2. `Event inventory & classification`
3. `Session funnel` — step rates + biggest leak
4. `Recommendations (events)` — ADD / FIX / REMOVE, each tied to a gap
5. `Data gaps & suggested improvements`

## Which skill for which data shape

| Your table looks like | Use |
| --- | --- |
| GA4 raw export (`event_name`, `event_params`, `items`, `traffic_source`, `device`, `geo`) | `ga4-tracking-audit` → `ga4-events` → `ga4-segmentation` |
| Flat events table (`event_type`, `session_id`, `sequence_number`, `uri`) | `ga4-events` adapted (it has the generic templates) |
| Anything else / unknown schema | Start with `ga4-tracking-audit`; run the schema check first |

The skills work on **ecommerce or SaaS** GA4 exports. Set
`{property_type} = ecommerce | saas` once per run; it selects the funnel steps
and the expected-param scoring matrix (ecommerce: `view_item` →
`add_to_cart` → `purchase`; SaaS: `sign_up` → `pricing_view` → `start_trial`
→ `subscribe`). Both branches share the same quality gate, dedupe, and
coverage machinery — only the event lists differ. Always adapt to the
property's *actual* event names from the inventory; the templates are
starting points, not a spec.

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
| `ga4-tracking-audit` / `ga4-events` | `{project_id}`, `{dataset}`, `{start}`/`{end}` (YYYYMMDD), `{property_type}` (`ecommerce`/`saas`) |
| `ga4-segmentation` | same + `{segment_dim}` (source / device / geo / new vs returning) |

### 6. Known data quirks to watch for (learned from real runs)

- **`user_id` missing ≠ clean data.** In many exports it's empty (cookies
  only); in flat lookup-style tables it can exist *only on buyers* — per-user
  funnels are meaningless in both cases. Always check.
- **Zero-param counts hide partial tracking.** Events can fire but carry no
  `items`/`value`/`currency` (e.g. a checkout step with 0% items). The
  coverage matrix in `ga4-tracking-audit` catches this.
- **`value` can be any numeric param type.** Check `int_value` +
  `float_value` + `double_value` (and the `event_value_in_usd` column in newer
  exports) — reading only one type under-reports purchase value by 30%+.
- **`ecommerce.transaction_id` can be a constant placeholder.** On
  `ga4_obfuscated_sample_ecommerce` it's the *same value on every event*
  (cardinality 1) except on `purchase`. The §3b hygiene check catches this —
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
  previous step (e.g. cart→checkout) usually fires on page load, not on the
  user action — check the event's trigger before reporting it as a conversion
  step.

## When the Gastronomixs data becomes available

1. Confirm the GCP project + dataset + schema (step 1–3 above).
2. Run `ga4-tracking-audit` → establishes the **tracking health score** and a
   prioritized fix list (GTM vs code).
3. Run `ga4-events` → funnel + which events to add/fix/remove.
4. Run `ga4-segmentation` → which segments (traffic source / device / geo /
   new vs returning) convert best and where they leak.
5. Every report ends with concrete, actionable recommendations tied to
   observed gaps — ready to hand to the tracking owner.
