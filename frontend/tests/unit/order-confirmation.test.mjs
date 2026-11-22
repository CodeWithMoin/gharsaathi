import assert from "node:assert/strict";
import test from "node:test";
import { applyAssistantEvent } from "../../app/assistant-events.mjs";
import {
  countdownLabel,
  deliverToLabel,
  orderCardIndex,
  orderCardResultText,
  orderCardStateFromResponse,
  orderConfirmationFromEvent,
} from "../../app/order-confirmation.mjs";

const event = (overrides = {}) => ({
  type: "order.confirmation_requested",
  provider: "zepto",
  store: "Zepto",
  confirmation: {
    id: "conf_abcdefgh1234",
    expires_at: "2026-11-22T10:05:00Z",
    payable: "₹222",
    payable_paise: 22200,
    item_count: 2,
    items: [{ name: "Fortune Sunflower Oil", pack_size: "1 L", quantity: 2 }],
    payment: "cash_on_delivery",
    delivery_address: { label: "Home", address: "Green Park" },
    ...overrides,
  },
});

test("a valid order event becomes the card's confirmation", () => {
  const confirmation = orderConfirmationFromEvent(event());
  assert.deepEqual(confirmation, {
    id: "conf_abcdefgh1234",
    provider: "zepto",
    store: "Zepto",
    expiresAt: "2026-11-22T10:05:00Z",
    payable: "₹222",
    payablePaise: 22200,
    itemCount: 2,
    items: [{ name: "Fortune Sunflower Oil", packSize: "1 L", quantity: 2 }],
    addressLabel: "Home",
    address: "Green Park",
  });
  assert.equal(deliverToLabel(confirmation), "Deliver to: Home");
});

test("malformed order events are ignored", () => {
  assert.equal(orderConfirmationFromEvent(null), null);
  assert.equal(orderConfirmationFromEvent({ ...event(), type: "basket.updated" }), null);
  assert.equal(orderConfirmationFromEvent({ ...event(), provider: "bigbasket" }), null);
  assert.equal(orderConfirmationFromEvent(event({ id: "x" })), null);
  assert.equal(orderConfirmationFromEvent(event({ id: "has spaces in it" })), null);
  assert.equal(orderConfirmationFromEvent(event({ expires_at: "soon" })), null);
  assert.equal(orderConfirmationFromEvent(event({ payable_paise: 222.5 })), null);
  assert.equal(orderConfirmationFromEvent(event({ items: [] })), null);
  assert.equal(orderConfirmationFromEvent(event({ items: [{ name: "Oil", quantity: 0 }] })), null);
});

test("the countdown reads minutes:seconds and ends at expiry", () => {
  const expires = "2026-11-22T10:05:00Z";
  assert.equal(countdownLabel(expires, Date.parse("2026-11-22T10:00:01Z")), "Expires in 4:59");
  assert.equal(countdownLabel(expires, Date.parse("2026-11-22T10:04:51Z")), "Expires in 0:09");
  assert.equal(countdownLabel(expires, Date.parse("2026-11-22T10:05:00Z")), null);
});

test("only a provider-confirmed order id counts as placed", () => {
  assert.deepEqual(orderCardStateFromResponse({ ok: true, body: { status: "confirmed", provider_order_id: "ZEP-1" } }), { status: "placed", orderId: "ZEP-1" });
  assert.deepEqual(orderCardStateFromResponse({ ok: true, body: { status: "confirmed", provider_order_id: null } }), { status: "unknown" });
  assert.deepEqual(orderCardStateFromResponse({ ok: true, body: { status: "outcome_unknown" } }), { status: "unknown" });
  assert.deepEqual(orderCardStateFromResponse({ ok: false, status: 409, message: "This order card expired." }), { status: "failed", reason: "This order card expired." });
  assert.deepEqual(orderCardStateFromResponse({ ok: false, status: 502, message: "Zepto could not refresh this bill." }), { status: "failed", reason: "Zepto could not refresh this bill." });
  // A dropped connection or unexpected server error is never "not placed": the user must not tap again blindly.
  assert.deepEqual(orderCardStateFromResponse({ ok: false, status: 0, message: "" }), { status: "unknown" });
  assert.deepEqual(orderCardStateFromResponse({ ok: false, status: 500, message: "boom" }), { status: "unknown" });
});

test("result lines are plain English", () => {
  assert.equal(orderCardResultText({ status: "placed", orderId: "ZEP-1" }, "Zepto"), "Order placed · #ZEP-1");
  assert.equal(orderCardResultText({ status: "failed", reason: "The bill changed." }, "Zepto"), "Order not placed: The bill changed.");
  assert.match(orderCardResultText({ status: "cancelled" }, "Zepto"), /Nothing was ordered/);
  assert.match(orderCardResultText({ status: "unknown" }, "Zepto"), /hasn't confirmed/);
  assert.equal(orderCardResultText({ status: "open" }, "Zepto"), null);
});

test("the card goes after its anchor message and that message's cards", () => {
  const items = [
    { kind: "message", message: { id: "m1" } },
    { kind: "basket" },
    { kind: "message", message: { id: "m2" } },
  ];
  assert.equal(orderCardIndex(items, "m1"), 2);
  assert.equal(orderCardIndex(items, "m2"), 3);
  assert.equal(orderCardIndex(items, "gone"), 3);
  assert.equal(orderCardIndex(items, null), 3);
});

test("the assistant event opens the card and hands a fresh bill to the basket panel", () => {
  const calls = [];
  const money = (paise) => ({ paise, display: `₹${paise / 100}` });
  const request = {
    approval: { id: "apr-1", fingerprint: "fp", payable: money(22200), expires_at: "2026-11-22T10:10:00Z" },
    quote: { item_total: money(19700), charges: [], payable: money(22200) },
    basket: { provider: "zepto", lines: [] },
  };
  const handlers = {
    onOrderConfirmation: (confirmation) => calls.push(["order", confirmation.id]),
    onApprovalRequested: (provider, value) => calls.push(["approval", provider, value.approval.id]),
  };
  const outcome = applyAssistantEvent({ ...event(), request }, handlers);
  assert.equal(outcome.kind, "order_confirmation");
  assert.match(outcome.message, /Place order/);
  assert.deepEqual(calls, [["approval", "zepto", "apr-1"], ["order", "conf_abcdefgh1234"]]);

  calls.length = 0;
  applyAssistantEvent(event(), handlers);
  assert.deepEqual(calls, [["order", "conf_abcdefgh1234"]]);
  assert.equal(applyAssistantEvent(event({ id: "bad" }), handlers), null);
});
