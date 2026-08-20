# GA4 Analytics Report — Public Sample Dataset

* **Target dataset:** `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
* **Primary window (last full month):** `20201101` – `20201130` (Nov 2020; Dec 1–20 present but partial)
* **Trend / retention window:** `20201101` – `20201220` (all 50 daily tables)
* **Billing project:** `project-7b13beb9-4f8f-4556-8e0`
* **Executed:** 2026-08-20

---

## 0. Quality gate (all skills)

| check | result | evidence (Nov window) |
| --- | --- | --- |
| duplicate rate | **PASS** | 0 / 1,472,712 events (distinct_keys = events) |
| null `user_pseudo_id` | **PASS** | 0 / 1,472,712 (0.0%) |
| null session key | **PASS** | 0 / 1,472,712 (0.0%) — `ga_session_id` param used (no first-class `session_id` column) |
| session integrity | **PASS** | 105,380 sessions; single-event share 0.1% (≤10%); >100-event share 1.9% (≤5%); max 726 events |
| zero-param events | **PASS** | 0 |
| `user_id` | **NOTE** | empty on 100% of rows — all user-level numbers count cookies, not people |

**Verdict: data is clean at the event level; no dedupe needed.** Identity is cookie-scoped throughout.

**Property-type inference:** `ecommerce` (view_item / add_to_cart / begin_checkout / purchase roles; no signup/trial/subscribe/cancel roles observed).

---

## 1. ga4-tracking-audit

### Discovery — event inventory (Nov 2020)

| event | n | distinct users | distinct pages | role |
| --- | --- | --- | --- | --- |
| page_view | 453,904 | 79,181 | 1,618 | marker |
| user_engagement | 407,196 | 65,757 | 1,435 | engagement (noise) |
| scroll | 170,855 | 48,787 | 1,100 | engagement (noise) |
| view_item | 148,639 | 21,440 | 473 | product view |
| session_start | 106,585 | 78,383 | 819 | visit |
| first_visit | 71,773 | 71,734 | 632 | visit marker |
| view_promotion | 66,789 | 36,139 | 19 | promotion |
| begin_checkout | 9,546 | 4,219 | 4 | checkout begin |
| view_search_results | 8,881 | 4,616 | 2 | search |
| add_to_cart | 7,674 | 2,060 | 34 | cart add |
| add_shipping_info | 7,492 | 4,217 | 3 | payment info |
| add_payment_info | 5,125 | 2,121 | 1 | payment info |
| select_promotion | 3,012 | 2,621 | 5 | promotion |
| select_item | 2,369 | 1,198 | 35 | item select |
| purchase | 2,054 | 1,532 | 2 | purchase |
| click | 763 | 489 | 30 | engagement (noise) |
| view_item_list | 55 | 37 | 8 | list view |

### Param coverage (meaningful params) + health score

| event | role | params measured | coverage | defect |
| --- | --- | --- | --- | --- |
| view_item | product view | items | 54.7% | **DEFECT** — <95% |
| view_item_list | list view | items | 65.5% | **DEFECT** + near-untracked (55 vs 2,369 select_item) |
| select_item | item select | items | 100% | — |
| add_to_cart | cart add | items / currency / value | 100% / 0% / 0% | **DEFECT** — no currency/value |
| begin_checkout | checkout begin | items / currency / value | 29.1% / 0% / 0% | **DEFECT** — structural on conversion |
| add_shipping_info | payment info | items / currency / value | 0% / 100% / 0% | **DEFECT** — items+value 0% |
| add_payment_info | payment info | items / currency / value | 0% / 100% / 0% | **DEFECT** — items+value 0% |
| purchase | purchase | value / currency / transaction_id | 97.3% / 100% / 97.3% | — (best-tracked event) |
| view_search_results | search | search_term | 100% | — |
| view_promotion | promotion | promotion_id / creative_name / items | 0% / 0% / 68.4% | **DEFECT** |
| select_promotion | promotion | promotion_id / creative_name / items | 0% / 0% / 92.0% | **DEFECT** |

**Tracking health score = 8 / 25 = 32%** (params present ≥95% ÷ params expected on observed role-mapped events; missing roles not scored).

### Transaction-id placeholder check (§6a)
* `ecommerce.transaction_id` on **every non-purchase event is a cardinality-1 constant placeholder** (e.g. `view_item` 143,920 rows, 1 distinct value) → never read as real transaction coverage.
* On `purchase`: 2,031 rows with `ecommerce.transaction_id` and **1,259 distinct real values**, plus `event_value_in_usd` on 97.3% → purchase revenue is trustworthy. Nuance: the `transaction_id` **param key** is present on 97.3% of purchases, but only 75.1% carry a non-null **string value** (456 rows have the key with an empty/other-typed value) — so the health-score "present" count is key-level.

### Missing recommended events (suggested additions)
* `view_item_list` impressions are effectively untracked (55 events vs 2,369 `select_item`; list-view role <10% of item-select volume → **FAIL** per threshold). Wire impressions on category pages.
* No `purchase_error`, `remove_from_cart`, `view_cart` events — cart→checkout leak can't be localized.

### Fix routing
* **GTM:** forward existing `ecommerce.items` dataLayer to `add_payment_info` / `add_shipping_info` / `begin_checkout` (items 0–29%); add `currency`/`value` to `add_to_cart` + checkout steps; wire `view_item_list` impressions; add `promotion_id`/`creative_name` on promotion events.
* **App code:** push `value`/`currency`/`transaction_id` on `purchase` (already strong, 97%); ensure `items` is populated before `begin_checkout` fires.
* **Schema/config:** `user_id` empty everywhere → cookie-based identity; enable `user_id` + Google Signals for cross-device.

---

## 2. ga4-tracking-trend

Trend window Nov 1 – Dec 20, weekly buckets (`WEEK(MONDAY)`). Every bucket: **dup rate 0%, null keys 0%, zero-param share 0%** — tracking quality is perfectly stable.

| week starting | events | dup | null pseudo | null session | zero-param | event types |
| --- | --- | --- | --- | --- | --- | --- |
| 2020-10-26 (partial) | 31,272 | 0 | 0 | 0 | 0 | 16 |
| 2020-11-02 | 327,328 | 0 | 0 | 0 | 0 | 15 |
| 2020-11-09 | 312,650 | 0 | 0 | 0 | 0 | 15 |
| 2020-11-16 | 354,817 | 0 | 0 | 0 | 0 | 16 |
| 2020-11-23 | 376,031 | 0 | 0 | 0 | 0 | 17 |
| 2020-11-30 | 442,688 | 0 | 0 | 0 | 0 | 17 |
| 2020-12-07 | 505,813 | 0 | 0 | 0 | 0 | 17 |
| 2020-12-14 (partial) | 394,395 | 0 | 0 | 0 | 0 | 16 |

**Health trend:** flat at ~32% (same tracking defects every week). **No silent regressions.** Only signal is **volume growth** (+55% weekly events Nov 2 → Dec 7, holiday/Black-Friday peak) — seasonal, not a tracking change.

---

## 3. ga4-events

### Event classification
* **Indicative / useful (10):** `view_item`, `select_item`, `add_to_cart`, `begin_checkout`, `add_shipping_info`, `add_payment_info`, `purchase`, `view_search_results`, `select_promotion`, `view_promotion`.
* **Noise / markers (6):** `page_view`, `user_engagement`, `scroll`, `click`, `session_start`, `first_visit`.
* `click` (763) and `view_item_list` (55) are near-dead weight; `click` duplicates page-load params with no consumer.

### Session funnel (discovered events, Nov 2020)

| step | event used | sessions | % of sessions | step-to-step |
| --- | --- | --- | --- | --- |
| 1 | session_start | 103,648 | 98.4% | — |
| 2 | view_item | 25,717 | 24.4% | 24.8% of s1 |
| 3 | select_item | 1,304 | 1.2% | 5.1% of s2 |
| 4 | add_to_cart | 2,178 | 2.1% | 167% of s3* |
| 5 | begin_checkout | 4,575 | 4.3% | 210% of s4* |
| 6 | add_shipping_info | 4,573 | 4.3% | 100.0% of s5 |
| 7 | add_payment_info | 2,389 | 2.3% | 52.2% of s6 |
| 8 | purchase | 1,616 | 1.5% | 67.6% of s7 |

\* select_item and add_to_cart are not a strict sequence — many add-to-cart sessions skip select_item (it's under-tracked). begin_checkout > add_to_cart confirms **begin_checkout fires on page load** (yourinfo.html), not on a user action.

**Biggest real leak: view_item → add_to_cart** — only 8.5% of product-view sessions ever add to cart (2,178/25,717). **Overall session→purchase conversion: 1.5%.**

### Recommendations (events)
* **ADD:** `view_cart`, `remove_from_cart`, `purchase_error` to localize the cart→checkout leak; rewire `begin_checkout` to the real checkout-start click instead of page load.
* **FIX:** `select_item` tracking (5.1% of view_item is implausibly low for a working shop) — likely under-fired GTM trigger; add `items`/`value`/`currency` to checkout steps.
* **REMOVE:** `click` (763, no consumer) or attach a meaningful param.

### Data gaps
* No first-class `session_id` / `page_location` columns (params only); `user_id` empty; `view_item_list` impressions untracked; synthetic-sample rigid session shapes (items avg inflated, page-load-driven checkout steps).

---

## 4. ga4-segmentation

Baseline (all sessions, Nov): view_item 24.4% · add_to_cart 2.1% · begin_checkout 4.3% · add_payment_info 2.3% · **purchase 1.5%**. Segment columns are 100% populated (0 nulls on source/device/country/platform).

### Device × funnel

| segment | sessions | view_item | add_to_cart | checkout | payment | **purchase** | check→purch |
| --- | --- | --- | --- | --- | --- | --- | --- |
| desktop | 61,773 | 24.2% | 2.1% | 4.3% | 2.2% | 1.5% | 34.6% |
| mobile | 42,670 | 24.2% | 2.0% | 4.3% | 2.3% | 1.6% | 36.5% |
| tablet | 2,378 | 24.1% | 2.0% | 4.1% | 2.3% | 1.3% | 32.7% |

**Flat (<0.3pp spread) — no device problem.** Site-wide funnel, not segment.

### Traffic source × funnel

| segment | sessions | view_item | add_to_cart | checkout | payment | **purchase** | check→purch |
| --- | --- | --- | --- | --- | --- | --- | --- |
| google | 37,181 | 24.1% | 1.9% | 4.1% | 1.8% | 1.2% | 29.8% |
| `<Other>` | 28,320 | 23.8% | 2.1% | 4.2% | 2.0% | 1.3% | 31.9% |
| (direct) | 24,881 | 23.7% | 1.9% | 4.1% | 2.1% | 1.4% | 34.8% |
| shop.googlemerchandisestore.com* | 9,239 | 24.6% | 2.2% | 4.9% | 3.1% | 2.2% | 44.3% |
| (data deleted)* | 7,900 | 25.2% | 2.4% | 5.6% | 4.5% | 3.1% | 54.8% |

\* **Attribution artifacts — inflated conversion is noise, not a channel win** (self-referral + (data deleted)). Real channels are within ~0.2pp of baseline → flat.

### New vs returning (cookie-based)

| segment | sessions | view_item | add_to_cart | checkout | payment | **purchase** | check→purch |
| --- | --- | --- | --- | --- | --- | --- | --- |
| new | 71,477 | 24.0% | 2.0% | 3.9% | 1.3% | 0.9% | 21.9% |
| returning | 35,028 | 24.7% | 2.2% | 5.1% | 4.2% | 2.9% | 56.1% |

User-level conversion: **new 0.83% (608/73,018) vs returning 4.70% (949/20,207) → returning converts 5.7× better.** Returning users leak far less at checkout→purchase (56% vs 22%). **This is the strongest segment signal.**

### Geo × funnel (top 12)
All countries 1.2–1.8% purchase rate — **flat**. Canada (39.2% check→purch) and France (40.9%) slightly better at checkout; Italy underperforms (26.0% check→purch) but small sample. No actionable geo gap.

### Best & worst segments
* **Best:** returning users (5.7× conversion) — retention is the lever; (direct) modestly better than paid google (1.4% vs 1.2%).
* **Worst:** new users (0.9% purchase) leak at checkout→purchase; `google` session conversion slightly below baseline.
* Practical implication: optimize checkout for **first-time buyers** (they're the leak); avoid reading self-referral / (data deleted) as wins.

### Recommendations (to strengthen segmentation)
* **ADD:** `user_id` (SaaS-grade identity) — without it returning/new is cookie-based; `transaction_id`+`value` per segment for revenue-by-segment; `item_category` on product events.
* **FIX:** populate `traffic_source` consistently (single-touch, session-start only) — segment attribution depends on which row you sample.

---

## 5. ga4-retention-cohorts

Window Nov 1 – Dec 20 (cohorts = first-touch week; activity through Dec 20). Cookie-based; pre-November first-touch cohorts are stale-cookie artifacts (excluded below). **ecommerce property — no signup/trial/subscribe/cancel roles exist**, so SaaS activation / trial-to-paid / churn metrics don't apply.

### Weekly cohort retention

| cohort week | size | wk0 | wk1 | wk2 | wk3 | wk4 |
| --- | --- | --- | --- | --- | --- | --- |
| 2020-11-02 | 16,496 | 100.0% | 4.3% | 1.9% | 1.4% | 1.2% |
| 2020-11-09 | 14,598 | 99.9% | 5.7% | 2.4% | 1.7% | 1.8% |
| 2020-11-16 | 16,901 | 99.9% | 5.3% | 2.8% | 2.0% | 1.5% |
| 2020-11-23 | 18,738 | 100.0% | 4.6% | 2.1% | 1.3% | — |
| 2020-11-30 | 21,192 | 100.0% | 5.1% | 2.1% | — | — |
| 2020-12-07 | 27,560 | 100.0% | 3.1% | — | — | — |

wk0 ≈ 100% is a window artifact (users are in the window by definition). True signal: **wk1 3–6%, wk2 ~2%, wk3–4 ~1.5–2% — very low, flat retention.** New users almost never come back; cohorts are stable (no onboarding improvements, no degradation).

### Activation / trial-to-paid
Not applicable — no activation, trial, or subscribe events exist in this ecommerce export. For SaaS, `tutorial_complete` / `start_trial` / `subscribe` would be the suggested additions.

### Repeat purchase (ecommerce proxy)
`purchase` buyers in Nov: 1,532 distinct users of 79,421 total (1.9% of users ever purchased). Repeat-buyer / time-to-first-purchase query aborted mid-run — re-run `buyers.json` query if needed.

### Churn
**Untracked** — no cancel/churn event exists. For this ecommerce sample that's expected; for a SaaS property `cancel_subscription` (with `plan`+`reason`) would be the suggested addition.

### Insights & recommendations
* Retention is the biggest opportunity: new users convert at 0.8% and 96%+ never return after wk1. The cohort curves are flat, so this is a product/onboarding problem, not a regression.
* **ADD (suggested):** activation event (e.g. `tutorial_complete` / first-purchase milestone) — the leading indicator of retention; `user_id` on all events (cookie-based cohorts understate retention).

---

## 6. ga4-kpi-snapshot (November 2020)

### Headline KPIs

| metric | value | vs previous period |
| --- | --- | --- |
| DAU (avg) | 3,129 | +11.5% (last-7 vs prior-7) |
| WAU (as of 11-30) | 22,649 | +11.5% |
| MAU (as of 11-30) | 79,421 | n/a (window = 30d) |
| Stickiness WAU/MAU | 28.5% | — |
| Stickiness DAU/MAU | 3.9% | — |
| Sessions (total) | 106,602* | +7.7% |
| Engagement rate | 79.4% | −0.9pp |
| Purchase conversion (sessions) | 1.5% | +55% purchasers (last-7 vs prior-7) |
| New vs returning sessions | 71,773 / 34,812 | — |

> Previous-period delta: the dataset starts 2020-11-01, so no October baseline exists. Deltas are **intra-month** (Nov 24–30 vs Nov 17–23).
> \* `Sessions (total)` 106,602 is the **sum of per-day distinct sessions**; a session crossing midnight is counted in both daily partitions. **True unique sessions = 105,380** (matches the funnel/session-integrity query).

### Daily series (compact)
DAU ranged **1,919–4,648** (min Nov 8 Sun, max Nov 3 Tue); sessions 2,154–5,244; engagement rate 79.4% average, stable all month. Weekend trough and weekday peak are consistent — no anomalies.

### Acquisition
* New sessions 71,773 (67%) vs returning 34,812 (33%) — cookie-based.
* **Top pages:** home `shop.googlemerchandisestore.com/` (236,644) → `basket.html` (72,093) → `store.html` (71,114) → `signin.html` (41,695); catalog pages next.
* **Top events:** page_view (453,904) > user_engagement (407,196) > scroll (170,855) > view_item (148,639) > session_start (106,585).

### Macro funnel (discovered steps)
session_start 103,648 → view_item 25,717 → add_to_cart 2,178 → begin_checkout 4,575 → add_payment_info 2,389 → **purchase 1,616 (1.5% of sessions, 67.6% of payment step)**.

### Notable changes & caveats
* Purchase count nearly **doubled** in the last 7 days (562 vs 362 purchasers, +55%) — holiday ramp.
* **Cookie-based identity** (user_id empty) — DAU/WAU/MAU count cookies, not people.
* Quality gate PASS — numbers are not inflated by duplicates.

---

## 7. Cross-skill recommendations (priority order)

1. **Fix checkout-item coverage** (health 32%): GTM-forward `items` + `value`/`currency` to `begin_checkout`/`add_shipping_info`/`add_payment_info` — biggest structural defect on the conversion path.
2. **Rewire `begin_checkout`** to a real click (page-load driven today) and fix `select_item` (implausibly low) — the funnel's #2–3 steps are unreliable.
3. **Track `view_item_list` impressions** (55 events vs 2,369 selects) — list performance is blind.
4. **Retain new users** (0.9% session / 0.83% user purchase; 96% never return): returning users convert 5.7× — invest in first-purchase activation + post-purchase loop; add an activation event + `user_id` for true cohorts.
5. **Ignore attribution artifacts** (`shop.googlemerchandisestore.com`, `(data deleted)`) in all reporting — their inflated conversion is noise.