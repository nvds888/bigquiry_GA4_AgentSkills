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

> **All shared SQL, thresholds, and the scoring matrix live in
> `.opencode/skills/ga4-events/reference/sql-templates.md`** (single source of
> truth for both skills) — Read it before querying. Section numbers below
> refer to that file.

## Parameters

Same as `ga4-events`: `{project_id}`, `{dataset}`, `{start}`, `{end}`, plus
`{property_type}` (`ecommerce` or `saas`) — selects the expected-event set and
scoring matrix from `sql-templates.md` §4.

## Workflow

1. **Cost-safe setup**: date-window filter on `_TABLE_SUFFIX`, dry-run,
   `--maximum_bytes_billed`. Same rules as `ga4-events`.
2. **Quality gate**: run the dup / null-key / session-integrity checks from
   `reference/sql-templates.md` §1. Record PASS/FAIL. If dup rate > 0.5%, run
   every query below against the dedupe pattern (§2).
3. **Recommended-event presence**: expected GA4 recommended events vs the
   inventory. Each missing event hides a funnel stage.
   - `{property_type} = ecommerce`: `session_start`, `first_visit`,
     `view_item_list`, `view_item`, `select_item`, `add_to_cart`,
     `remove_from_cart`, `view_cart`, `begin_checkout`, `add_shipping_info`,
     `add_payment_info`, `purchase`,
     `refund`, `search`, `select_promotion`, `view_promotion`.
   - `{property_type} = saas`: `session_start`, `first_visit`, `sign_up` (or
     `create_account`), `login`, `generate_lead`, `pricing_view`,
     `start_trial`/`trial_started`, `subscribe` (or `purchase`), `upgrade`,
     `cancel_subscription`, `refund`, `search`, `tutorial_complete`.
4. **Required-param coverage matrix**: run the SQL in `reference/sql-templates.md`
   §3 and compare against the expected-param matrix (§4a for `ecommerce`, §4b
   for `saas`). Report coverage % per event.
   The §3 query reads `value` from `int_value`/`float_value`/`double_value` and the
   `event_value_in_usd` column — don't downgrade to a single value type.
5. **`items`-array integrity**: run the SQL in §5, plus list-impression vs
   click consistency (§6: `view_item_list` vs `select_item`). Also run the
   **§3b transaction-id hygiene check**: an `ecommerce.transaction_id` column
   with cardinality 1 across a high-volume event is a constant placeholder
   (see worked example) — flag it and exclude it from coverage, don't count it.
   For `saas`, the parallel checks are `plan` + `value` + `currency` coverage
   on `subscribe`/`start_trial` (§4b) instead of `items`.
6. **Duplicate / renamed events**: same semantic event under multiple names
   (e.g. `addtocart` + `add_to_cart`), custom events duplicating recommended
   ones (same `page_location`), events that look like debug/bucket leftovers.
7. **Session anomalies**: single-event share, sessions > 100 events, missing
   `session_start`, broken `ga_session_id` (see §1c).
8. **Score + fix list** (see Scoring and Fix routing below).

## Scoring (tracking health)

Health score = `100 * (params present / params expected)` summed over the
expected events for the property type. The expected-param matrix and the
scoring rule ("a param counts as present only at ≥ 95% coverage") are defined
in `reference/sql-templates.md` §4a (`ecommerce`) / §4b (`saas`), with a
worked example in §7. Report:

- overall score;
- per-event coverage table (`event | expected params | % complete | defect`);
- anything < 100% on a commerce/SaaS conversion event is a **defect**, not a
  behavior — it silently truncates funnels and revenue attribution.

### Explicit PASS/FAIL thresholds (from `reference/sql-templates.md`)

| check | rule |
| --- | --- |
| duplicates | **PASS ≤ 0.5%** dup rate; above → dedupe before all analysis |
| null join keys | **PASS ≤ 0.5%** for `user_pseudo_id` and `session` null share |
| sessionization | **PASS** single-event share ≤ 10% and >100-event share ≤ 5% |
| `items` coverage | **DEFECT** < 95% on any commerce event (0% = structural) |
| `currency`/`value`/`transaction_id` | **DEFECT** < 95% on the events that require them |
| list impressions | **FAIL** if `view_item_list` < 10% of `select_item` |
| `user_id` | note when 0 rows carry it → all user numbers are cookie-based |

### Worked example — `ga4_obfuscated_sample_ecommerce`, Nov–Dec 2020

`view_item` 59% items, `add_to_cart` 100% items but **0% value**, `begin_checkout` 72% items / 0% value, `add_shipping_info`/`add_payment_info` 0% items + 0% value, `purchase` value 97% / transaction_id ~99% (real distinct values; the `ecommerce.transaction_id` **column** is a cardinality-1 placeholder on every other event), `view_item_list` 68% items + only 62 events vs 17,291
`select_item` → **health ≈ 41%** on that run (see §7 for the table). Every row
in that table maps to a GTM/code fix below.

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
