---
name: thelook-events
description: Analyze the thelook_ecommerce events table and classify which events are indicative and useful for analytics versus noise. Produces a standardized report (quality gate, event classification, session funnel, event add/fix/remove recommendations, data gaps and suggested improvements). Use when the user asks about The Look / thelook_ecommerce events, which events matter, event quality, event recommendations, data gaps, or wants event-level or funnel analysis of the BigQuery public dataset.
---

# The Look Events Analysis

Analyze the `bigquery-public-data.thelook_ecommerce.events` table and
classify each `event_type` as **indicative/useful** or **noise**, with
evidence from the data.

## Reference table

`thelook_ecommerce.events` (2,421,519 rows):

| column | type | notes |
| --- | --- | --- |
| `id` | INTEGER | unique event id |
| `user_id` | INTEGER | joins `users` |
| `sequence_number` | INTEGER | order of events within a session |
| `session_id` | STRING | browsing session |
| `created_at` | TIMESTAMP | event timestamp |
| `ip_address` | STRING | |
| `city` / `state` / `postal_code` | STRING | geo |
| `browser` | STRING | |
| `traffic_source` | STRING | e.g. Search, Email, Paid Search, Organic |
| `uri` | STRING | page path: `/home`, `/cancel`, `/cart`, `/product/{id}`, `/department/{dept}/category/{cat}/brand/{brand}` |
| `event_type` | STRING | one of `product`, `cart`, `department`, `purchase`, `cancel`, `home` |

Baseline distribution (verify with your own query, it may vary by as-of date):

| event_type | count | share |
| --- | --- | --- |
| product | 842,726 | 34.8% |
| cart | 592,583 | 24.5% |
| department | 592,580 | 24.5% |
| purchase | 180,954 | 7.5% |
| cancel | 124,987 | 5.2% |
| home | 87,689 | 3.6% |

## Classification rubric

**Indicative / useful** (signals of intent or outcome):

- `purchase` — the conversion event; the primary success metric. Always useful.
- `cancel` — negative outcome; used to compute cancellation rate, cart
  abandonment follow-up, refund/payment risk. Always useful.
- `cart` — strong purchase intent; backbone of conversion-funnel analysis.
- `product` — product-level interest; powers view-to-purchase, popularity,
  recommendations, and affinity models.

**Noise / low value** (weak intent or redundant):

- `home` — just an entrance page hit; nearly everyone hits it, so it adds
  little signal. Useful only as a session-start marker.
- `department` — category browsing; low purchase intent, noisy, and has
  essentially no product-specific signal. Exclude from most funnel/intent
  analyses. It does have one use: comparing category interest (views per
  category) vs. sales.

**Context-dependent:**

- If the analysis is about *traffic sources* or *geo*, `traffic_source`,
  `city`/`state`, and `browser` are the signal, and event type matters less.
- If the analysis is about *sessions*, use `home` as session start and
  `sequence_number`/`session_id` to order the journey, but still exclude
  `home`/`department` from intent scoring.

## Quality gate (run before trusting any rate)

1. Duplicates: `COUNT(*)` must equal `COUNT(DISTINCT id)`.
2. Join keys: `COUNTIF(session_id IS NULL)` and `COUNTIF(sequence_number IS NULL)` must be 0.
3. Null `user_id` broken down by `event_type`. **Known structural quirk**:
   `cancel` is 100% NULL; ~60% of `product`/`cart`/`department` are NULL;
   `home`/`purchase` are never NULL. User-level funnels are therefore
   meaningless — always work at session level.
4. Session shape: group by `session_id` and check `COUNT(*)` distribution.
   Known quirk: session lengths are only 1, 2, 3, 5, 7, 10, 13 events, and
   3-event sessions are exactly `product>cart>cancel` or
   `department>product>cart`. The dataset is template-driven; treat
   "behavioral" findings with caution.

## Workflow

1. Query the event distribution:
   `SELECT event_type, COUNT(*) AS n FROM \`bigquery-public-data.thelook_ecommerce.events\` GROUP BY event_type ORDER BY n DESC`
2. Break down by `uri` within each event type to confirm what each event maps
   to (e.g. `product` -> `/product/{id}`, `cancel` -> `/cancel`, `home` -> `/home`).
3. Classify each `event_type` using the rubric, citing the observed counts.
4. Build the **session funnel** (per `session_id`, `MAX(IF(event_type = '...'))`):
   sessions -> product view -> cart -> purchase, with `cancel` as a separate
   negative outcome. Report step-to-step drop-off, cart-to-purchase, and
   cancel rate. Confirm `purchase` and `cancel` never co-occur in a session.
5. If the question is about cancellation, diagnose cart outcomes:
   purchase / cancel / silent abandon, product views before each outcome,
   cart->cancel sequence gap, and category/price mix (join `products` on the
   id parsed from the `/product/{id}` uri).
6. State the classification, funnel numbers, and the practical implication
   (what to include in funnels, what to exclude as noise).

## Standardized output

Always produce the report in this fixed order with these headers:

1. `Quality gate` — PASS/FAIL per check + null-user share by event type.
2. `Event inventory & classification` — table of `event_type | count | share | classification | rationale`.
3. `Session funnel` — `step | sessions | % of sessions | drop-off`; plus cart-to-purchase and cancel rate.
4. `Recommendations (events)` — ADD / FIX / REMOVE, each tied to a gap above.
5. `Data gaps & suggested improvements` — schema/tracking holes and how to close them.

## Recommendations: events to add / fix / remove

**Add** (per the observed gaps):

- `begin_checkout` / `checkout_stage` — the whole cart->purchase path is a
  black box (purchases appear with zero intermediate steps). Add checkout
  micro-steps to find where the 58% cart drop-off happens.
- `remove_from_cart` and `cart_view` — cart edits are invisible; there is only
  a binary cart flag. (A `cart` event at the end of a 3-event session can mean
  "added" or "abandoned".)
- `search` (with `search_term`) — no way to measure intent-driven discovery.
- `add_payment_info` / `purchase_error` — payment failures cannot be
  distinguished from abandonment.
- Params on existing events:
  - `cart` should carry `product_id`, `quantity`, `price` — the `uri` is just
    `/cart`, so carted items cannot be joined to `products`.
  - `cancel` should carry `product_id`/`order_id` + `cancel_reason` —
    cancellation is currently 100% anonymous and reasonless, so it can't be
    analyzed or remediated.
  - `purchase` should carry `order_id`, `total_value`, `items` — joinable to
    `orders` for revenue/refund analysis.

**Fix**:

- Backfill `user_id` on all events (currently NULL on all cancels and ~60% of
  product/cart/dept views) so per-user and cross-session funnels are possible.

**Remove**:

- `department` — no product-specific signal, low intent. Replace with category
  params on `product` events (or a `view_item_list` event).
- `home` — keep only as a session-start marker; it adds no intent signal.

## Data gaps & suggested improvements

- `user_id` NULL on ~46% of rows (all 124,987 cancels, ~60% of
  product/cart/department views). Users are only identified after purchase;
  per-user funnels and retention are impossible.
- No product/category/price on `cart` and `cancel` events — carted/canceled
  items cannot be attributed to products.
- No event params / key-values — the schema is a flat enum; attributes can't be
  added without a schema change. Suggested: migrate to a GA4-style
  `event_params` array.
- No `order_id` on `purchase`/`cancel` — cannot join to `orders`/`order_items`
  for revenue, refund, or fulfillment analysis.
- Rigid session templates (see quality gate) — session lengths are limited to
  {1,2,3,5,7,10,13} and outcomes map 1:1 to fixed paths, indicating synthetic
  or over-simplified data. Real journeys need `session_start`/`session_end`
  events and a proper `session_id` lifecycle.
- Suggested: add event params, order references, cancel reason, checkout
  micro-steps, and backfilled user_id; enable a proper sessionization model.

## Querying from PowerShell (Windows)

`bq.cmd` mangles quotes and backticks. Reliable pattern:

```powershell
$env:Path = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin;" + $env:Path
& "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\bq.cmd" query '--project_id=<PROJECT>' '--use_legacy_sql=false' '--format=pretty' "SELECT ... FROM ``bigquery-public-data.thelook_ecommerce.events`` ..."
```

- Wrap the SQL in a PowerShell double-quoted string and use **doubled
  backticks** (``) for BigQuery backtick identifiers.
- Use SQL string literals with single quotes inside the SQL (`IN ('product')`)
  — double quotes get stripped by the cmd wrapper.
- Always pass `--project_id=<PROJECT>` (no default project is set) or queries
  fail with "Cannot start a job without a project id".
- `bq.cmd ls bigquery-public-data:thelook_ecommerce` lists tables; queries can
  reference the table directly via the public project path.
