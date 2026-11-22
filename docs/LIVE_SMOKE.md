# Live provider smoke tests

Mocked tests can't catch problems that only show up on the real providers, such as Zepto
changing its cash-on-delivery preview shape or Swiggy rate-limiting the account. This
opt-in suite runs the real backend service classes, using the locally stored encrypted
Swiggy/Zepto connections.

> **Rule:** nobody runs this suite without the consent of the person who owns the
> connected Swiggy/Zepto accounts. Both tiers make real requests on their accounts.
> Tier 2 changes their Zepto cart for a short time.

## Default behaviour

The live tests sit in `backend/tests/live/` and carry the pytest marker `live`. They are
**skipped** in `make test`, `make check` and a plain `pytest`. A live test runs only when
all of these are true:

1. `--run-live` is passed to pytest, **and**
2. `GHARSAATHI_LIVE_SMOKE=1` is set, **and**
3. the provider has a stored connection (`backend/.data/*_connection.enc`). Without one,
   the test is skipped and no encryption key is created.

Tier 2 (marker `live_cart`) also needs `GHARSAATHI_LIVE_SMOKE_CART=1`.

The offline tests for the harness (`tests/live/test_live_smoke_harness.py`) always run.

## Tier 1: read-only (`make live-smoke`)

```bash
make live-smoke            # asks you to type CONSENT
./scripts/live-smoke.sh --yes   # supervised, non-interactive
```

Each check makes one controlled request per provider:

| Check | Swiggy | Zepto |
| --- | --- | --- |
| Connection status | local store only | local store only |
| `tools/list` catalogue vs committed snapshot | `tests/fixtures/swiggy_tools_catalogue_*.json` (if present) | `tests/fixtures/zepto_tools_catalogue_2026-11-21.json` |
| Saved addresses (shape only) | `get_addresses` | `list_saved_addresses` |
| One product search (`atta`) parses into `Product` | `search_products` | `select_saved_address` + `search_products` |
| Payment methods (shape only) | — | `get_payment_methods` with `{}` |

**What it changes:** nothing in your cart. Zepto's search needs a selected delivery
address, so the app calls `select_saved_address` on the server, just as normal search
does.

**Catalogue drift:** a removed tool, an added tool, or a structural change to a tool's
input schema makes the test fail. A change to descriptions only is recorded but does not
fail the test. Each run writes the live catalogue to
`backend/.live-smoke/<provider>_tools_catalogue_<date>.json`. No Swiggy snapshot is
committed yet. On the first run, review that file and commit it as
`backend/tests/fixtures/swiggy_tools_catalogue_<date>.json`. Do the same for a reviewed
Zepto refresh, but only after the adapters have been checked against the change.

## Tier 2: cart-mutating (`make live-smoke-cart`)

```bash
make live-smoke-cart       # prints a warning and asks you to type REPLACE
```

This follows the same path as `ZeptoQuoteProvider.quote_unlocked`:

1. `view_cart`. The run is **refused unless the cart is empty**. Set
   `GHARSAATHI_LIVE_SMOKE_CART_RESTORE=1` to allow replacing a non-empty cart and
   restoring its lines afterwards.
2. Pick the saved address, then the cheapest available `milk` item.
3. `select_saved_address` → `update_cart(replaceCart=True)` with that one item →
   `create_order(confirmOrder=False, userAddressId=…)` → `quote_from_cod_preview`.
4. In a `finally` block, remove the item with `update_cart` (quantity 0), or restore the
   original cart. Then call `view_cart` to confirm the item count matches the starting
   cart.

If the cleanup can't be confirmed, the test fails and tells you to remove the item in the
Zepto app.

A second tier-2 check, `test_zepto_prepaid_previews_record_shapes_only`, seeds the
cart the same way, then calls `create_online_payment_order`, `create_wallet_order`
and `create_upi_reserve_pay_order`, each once with exactly
`{"confirmOrder": false, "userAddressId": …}`, records their shapes plus
content-free facts (`payment_method_is_cod`, `parses_with_cod_bill_rules`,
`payable_equals_cod_preview`, `cause_step`), and cleans up the cart in `finally`.

**It can never place an order.** Every provider call goes through `GuardedToolClient` in
`tests/live/harness.py`, which checks the call before the real client is touched:

- Only allow-listed tools can be called in each tier. For Zepto's cart tier, the extra
  tools are `view_cart`, `update_cart` and `create_order`.
- Payment contract discovery is allowed by exact name only: `get_payment_methods`
  (both tiers, no arguments) and, in the cart tier, `create_online_payment_order`,
  `create_wallet_order` and `create_upi_reserve_pay_order`.
- Every other name matching `confirm|checkout|pay|place|wallet|upi|cancel|refund|reserve`
  is always refused. `check_payment_status` is refused in every tier, and any `poll`
  argument at any depth must be exactly `False`, so payment polling can never start.
- Every `create_*` tool needs `confirmOrder` to be exactly `False`. Leaving it out, or
  sending `True`, `"true"` or `1`, is refused. The only other key allowed is
  `userAddressId` (no `useZeptoCash`, no `riderTip`).
- Any `*confirm*` argument at any depth must be exactly `False`.
- Cart-tier calls also check `GHARSAATHI_LIVE_SMOKE_CART=1` again at the moment of the
  call.

A blocked call raises `ForbiddenLiveCall`, which is a `BaseException`. Production code
like `quote_unlocked` catches `Exception`, so it can't hide this error. Unit tests show
that the production confirmation gateway can't get past the guard.

## Discovering the payment response contract

Online payment stays off until `VERIFIED_CONTRACT` in
`backend/app/commerce/zepto/payments.py` is filled in. With the account owner's
consent:

1. `make live-smoke` (tier 1). In `backend/.live-smoke/latest.json`, read
   `responses` where `tool == "get_payment_methods"` and the
   `payment_methods_shape` check.
2. Empty the Zepto cart in the app, then `make live-smoke-cart` (tier 2). Read
   `responses` for the three prepaid `create_*` tools and the
   `prepaid_preview_shapes` check.
3. The reports hold key paths and types only. The contract also needs a few
   enum values (the method identifiers and the preview `paymentMethod` values);
   read those with the owner in a supervised session, never into the report.
4. The shapes of a *confirmed* prepaid order and of `check_payment_status` can
   only be seen by paying for a real order. That needs the owner's explicit
   go-ahead for that exact order (docs/MVP_CHECKLIST.md step 16).

## Rate limits

The suite does not retry. On the **first** 429, or if a stored cooldown from earlier
traffic is still active, the guard stops all further calls and pytest ends the whole
session with exit code 3. The message gives the provider, the operation and the
Retry-After value. Swiggy reports Retry-After in seconds. Zepto doesn't send one; its
client applies its own 5-minute cooldown. The client's existing cooldown is also saved,
so the app won't hit the provider straight away either. Wait the stated time before you
run the suite again.

## Reading the report

Each run writes `backend/.live-smoke/report-<UTC timestamp>.json` and `latest.json`. This
folder is gitignored. The report has no content in it. It holds:

- `checks`: counts and booleans per check, such as `address_count`, `product_count`,
  `with_brand`, catalogue diff tool names, quote `parsed`, `charge_count`, and the
  cleanup result.
- `responses`: for each provider tool call, the response **shape**. That is the key path,
  then the JSON types, then how many times it was seen, plus the minimum and maximum
  array length. Errors are recorded by type only.
- `halt`: the rate-limit stop message, if there was one.

No values are written: no names, addresses, phone numbers, prices or IDs. Keys that look
like data (digits, `@`, spaces, very long) are collapsed to `<key>`. If Zepto's preview
is rejected, look at `responses` where `tool == "create_order"` and compare those paths
and types with `app/commerce/zepto/checkout.py`. `cod_preview_quote.cause_type` and
`cause_message` name the parser rule that failed.

## When to run

- After a provider announces changes, or when users report errors from a provider.
- Before merging changes to an adapter, parser or checkout path.
- Roughly weekly, as a drift check. Always get the account owner's consent first.
