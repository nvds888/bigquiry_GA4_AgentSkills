---
name: ga4-tracking-audit
description: Audit the tracking completeness and correctness of a GA4 raw BigQuery export — tracking health score, per-event parameter coverage, duplicate/renamed events, missing recommended events (suggested, never assumed), session anomalies, and a prioritized fix list split by GTM vs app code. Fully data-driven: the skill discovers the actual event inventory and parameter map from the export and audits what it finds. Use when the user asks whether their tracking is trustworthy, what tracking is broken or missing, data completeness, what events lack required parameters, what to fix in GTM vs in code, or a tracking health check.
---

# GA4 Tracking Audit

Answer one question: **can this GA4 export be trusted for analytics?** Checks
which recommended events are missing (**reported as suggestions**), which fire
but are incomplete (no `items`, no `value`, no `transaction_id`), which are
duplicated, and what to fix — and routes each fix to GTM or app code.

> **Core principle — discovery, not spec.** Nothing about the expected event
> set or required params is hardcoded. The audit discovers the event inventory
> and parameter map from the export, maps observed events to *roles* via the
> heuristic vocabulary, and audits the events that actually exist. Missing
> roles are *suggested additions* tied to the funnel stage they would unblock —
> never treated as events that must exist. Read
> **`../sql-templates.md`** (shared templates in the `skills/` folder) before
> querying — it is the single source of truth for the discovery queries,
> quality-gate, coverage, and scoring SQL plus thresholds, shared by all GA4
> skills.

## Parameters

Same as `ga4-events`: `{project_id}`, `{dataset}`, `{table}`, `{start}`,
`{end}`, plus `{property_type}` (`ecommerce` or `saas`, **optional** — inferred
from the inventory if omitted, see reference §4b).

## Workflow

1. **Cost-safe setup**: date-window filter on `_TABLE_SUFFIX`, dry-run,
   `--maximum_bytes_billed`. Same rules as `ga4-events`.
2. **Discover** (reference §1): event inventory with evidence (§1a),
   parameter map per event (§1b), page map (§1c), session-key check (§1d).
3. **Quality gate** (reference §2): run the dup / null-key / session-integrity
   checks. Record PASS/FAIL. If dup rate > 0.5%, run every query below against
   the dedupe pattern (§3).
4. **Role mapping** (reference §4a): label each non-noise observed event with
   its best-matching role; report the `event → role → evidence` table.
5. **Missing recommended events**: compare observed roles against the
   vocabulary roles expected for the inferred property type. Each missing role
   is a **suggestion** tied to the funnel stage it hides — never a hardcoded
   expectation. Report them as "suggested additions" with the gap they fix.
6. **Required-param coverage**: from the parameter map (§1b) and the
   meaningful-params column of the vocabulary (§4a), compute coverage per
   role-mapped event. Report coverage % per event. The param map is the
   evidence — if an event carries params the vocabulary doesn't list, count
   those as its value evidence.
7. **`items`-array integrity**: for role-mapped product/cart/checkout events
   (ecommerce) check `items` coverage from §1b; for SaaS the parallel checks
   are `plan` + `currency` (+ `value` as a first-payment signal) on
   subscribe/trial roles. Run the **§6a transaction-id placeholder check** when
   a subscribe/purchase role exists.
8. **Duplicate / renamed events**: same semantic role under multiple names,
   custom events duplicating recommended ones (same top `page_location` from
   §1c), events that look like debug/bucket leftovers.
9. **Session anomalies**: single-event share, sessions > 100 events, missing
   `session_start`, broken `ga_session_id` (reference §2c).
10. **Score + fix list** (see Scoring and Fix routing below).

## Scoring (tracking health)

Health score = `100 * (params present / params expected)` summed over the
**observed** role-mapped events (reference §7). A param counts as present only
at ≥ 95% coverage. Missing roles are *not* scored as defects (they are
suggestions) — but report how many expected roles have no observed event so the
score is read in context. Report:

- overall score;
- per-event coverage table (`event | role | params measured | % complete | defect`);
- anything < 100% on a conversion event is a **defect**, not a behavior — it
  silently truncates funnels and revenue attribution.

### Explicit PASS/FAIL thresholds (from `../sql-templates.md`)

| check | rule |
| --- | --- |
| duplicates | **PASS ≤ 0.5%** dup rate; above → dedupe before all analysis |
| null join keys | **PASS ≤ 0.5%** for `user_pseudo_id` and `session` null share |
| sessionization | **PASS** single-event share ≤ 10% and >100-event share ≤ 5% |
| param coverage | **DEFECT** < 95% on any meaningful param of a role-mapped event (0% = structural) |
| `user_id` | ecommerce: note it (cookie-based users). **saas: defect** — logged-in users should carry it |
| list impressions | **FAIL** if a list-view role event has < 10% the volume of an item-select role event (when both exist) |

### Worked example — `ga4_obfuscated_sample_ecommerce`, Nov–Dec 2020

The discovery-driven audit on that export found: `view_item` 61% items,
`add_to_cart` 100% items but 0% value, `begin_checkout` 76% items / 0% value,
`add_shipping_info`/`add_payment_info` 0% items + 0% value (structural),
`purchase` value 97% / transaction_id ~99% (real distinct values; the
`ecommerce.transaction_id` **column** is a cardinality-1 placeholder on every
other event), `view_item_list` essentially untracked (62 events vs 20,778
`select_item`) → **health ≈ 41%** on that run. Every row maps to a GTM/code fix
below.

## Fix routing (GTM vs app code)

Route each fix; never say "track it better" without saying where:

- **GTM only** (no app release):
  - forward the existing `ecommerce.items` dataLayer object to steps that
    omit it (`add_payment_info`, `begin_checkout`) — most common items defect;
  - canonicalize/merge duplicate event names; add/change GA4 tag triggers;
  - events driven by page load / URL / DOM (e.g. list views on category pages,
    `search` from the URL `?q=` param);
  - enrich params readable from DOM/URL (e.g. `item_list_name` from breadcrumb
    or page title).
- **App code**:
  - events whose data exists only in JS state or backend: cart contents,
    `order_id`/`transaction_id`, `value`/`currency`, payment status, `user_id`;
  - `purchase`/`refund`/`subscribe` (and payment failures) — recommend
    server-side (Measurement Protocol / server-side GTM) so the client can't be
    trusted for order totals or subscription events;
  - **`user_id` on all events for SaaS** — without it, every user-level number
    counts cookies;
  - pushing `ecommerce.items` in the first place if the dataLayer never
    receives it.
- **Cannot fix in tracking**: defects that need schema/config changes
  (`user_id` + Google Signals for cross-device identity, enabling
  `event_bundle_sequence_id`, session rules).

## Standardized output

1. `Quality gate` — PASS/FAIL + dup rate + null-key shares.
2. `Discovery` — event inventory summary + property-type inference + `event → role → evidence` mapping.
3. `Tracking health score` — overall score + per-event coverage table.
4. `Missing recommended events` — **suggested additions**, each mapped to the funnel stage it hides.
5. `Duplicate / orphan / low-value events` — list + why each is noise.
6. `Session anomalies` — with evidence.
7. `Prioritized fix list` — each item tagged GTM / code / schema, with what
   the fix unlocks.

## Querying from PowerShell (Windows)

Use the `bq.cmd` pattern from `ga4-events`: double-quoted SQL, doubled
backticks for identifiers, single-quoted string literals, always
`--project_id=<PROJECT>`.