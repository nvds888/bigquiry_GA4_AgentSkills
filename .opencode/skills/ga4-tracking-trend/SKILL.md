---
name: ga4-tracking-trend
description: Monitor tracking health of a GA4 raw BigQuery export over time — compare the quality gate (duplicate rate, null keys, zero-param share) and tracking health score across successive weeks or months to catch silent regressions (e.g. a GTM change that breaks event params without anyone noticing). Fully data-driven: it reuses the discovery queries per time bucket rather than hardcoding events. Use when the user asks whether tracking has degraded over time, when a regression started, what changed in data quality week over week, or a tracking health trend.
---

# GA4 Tracking Trend

The `ga4-tracking-audit` is point-in-time. This skill answers: **is tracking
health improving, stable, or silently degrading** — and *when* a regression
started, so you can correlate it with deploys or GTM changes.

> **Core principle — discovery, not spec.** Every bucket re-runs the shared
> discovery + quality-gate queries (reference §1, §2) and the health score
> (§7); nothing about the event set is hardcoded. Read
> **`../sql-templates.md`** (shared templates in the `skills/` folder) before
> querying — §10 (per-bucket gate), §1 (discovery), §2 (quality gate), §7
> (score) are the source of truth.

## Parameters

- `{project_id}` — billing project / where the export lives
- `{dataset}` — e.g. `analytics_123456789`
- `{table}` — GA4 export prefix `events_*`; always filter with `_TABLE_SUFFIX`.
- `{start}` / `{end}` — the full window to trend, YYYYMMDD. Recommend ≥ 6–8
  weeks so there are enough buckets to see a trend.
- `{bucket}` — `weekly` (default) or `monthly`; each bucket is one row in the
  trend.
- `{property_type}` — optional hint (`ecommerce`/`saas`), inferred if omitted.

## Cost-safety (always, before any query)

Same rules as `ga4-events`: `_TABLE_SUFFIX` filter, dry-run first,
`--maximum_bytes_billed` on the real run, `SELECT` only needed columns,
single-quoted SQL string literals, doubled backticks for identifiers, always
`--project_id`. **Trend queries scan the whole window once** (the per-bucket
query in §10 is a single pass) — that's the cost-efficient design; don't run
per-bucket loops.

## Workflow

1. **Cost-safe setup**: dry-run the §10 per-bucket query; set the cap.
2. **Discover once for the window** (reference §1): event inventory + role
   mapping — these define which events the health score measures per bucket
   (the event set is treated as stable across the window; if a new event
   appears mid-window, note it rather than re-scoring history).
3. **Per-bucket quality gate** (reference §10): one query returning per bucket
   → events, dup rate, null `user_pseudo_id` share, null session share,
   zero-param share. This is the primary trend signal.
4. **Per-bucket health score** (reference §7 + §10): reuse the §10 per-bucket
   query and the role mapping from step 2 to compute the score per bucket.
   Note: §10 groups by bucket but not event — run the score on a bucketed
   param map (`SELECT bucket, event_name, p.key, COUNT(*) ... GROUP BY 1,2,3`)
   when the health score per bucket is needed, and only for the events in the
   role mapping.
5. **Regression detection**:
   - **flag any bucket that deviates >2pp from the window median** on dup rate
     or null-session share (reference §10);
   - flag zero-param share jumping (an event stopped carrying params);
   - flag the health score dropping >5 points in one bucket vs the prior;
   - correlate the flagged buckets with the property's release/GTM calendar if
     the user can provide it (ask; don't invent dates).
6. **Report** in the standardized order below.

## Standardized output

1. `Window & buckets` — the range, bucket size, event set used (from discovery), property-type inference.
2. `Trend table` — `bucket | events | dup rate | null pseudo | null session | zero-param | health score` (one row per bucket).
3. `Regressions found` — each flagged bucket + which metric moved + magnitude + direction, and a hypothesis (deploy/GTM correlation if available).
4. `Health trend` — is the score up, down, flat; best and worst bucket.
5. `Recommendations` — suggested fixes tied to the regressions (reuse the fix routing from `ga4-tracking-audit`), phrased as suggestions.

## Interpretation guardrails

- **One bucket is noise, a run is a trend**: a single bad week can be a spike
  (release, campaign, bot). Only call a regression when ≥2 consecutive buckets
  deviate or the score trends 3+ buckets in one direction.
- **Volume changes skew shares**: a bucket with far fewer events (holiday,
  outage) can move dup-rate/zero-param shares without any tracking change —
  report event volume alongside so readers don't misread the shares.
- **New/removed events mid-window**: if the inventory differs between the
  first and last bucket, say so and decide with the user whether to score the
  stable subset only.
- **Quality gate first**: if the window's dup rate is high throughout, the
  trend is "consistently unclean" — that's a finding, not a reason to skip.

## Querying from PowerShell (Windows)

Use the `bq.cmd` pattern from `ga4-events`: double-quoted SQL, doubled
backticks for identifiers, single-quoted string literals, always
`--project_id=<PROJECT>`.