import assert from "node:assert/strict";
import test from "node:test";
import { applyAssistantEvent } from "../../app/assistant-events.mjs";
import {
  autoCheckDelayMs,
  checkableCheckoutId,
  checkableOrderId,
  orderCheckDelayMs,
  placeButtonLabel,
  safePaymentUrl,
  withPaymentMethodCard,
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
    paymentMethod: "cod",
    paymentMethods: [{ id: "cod", label: "Cash on delivery" }],
    basketId: null,
    basketRevision: null,
  });
  assert.equal(deliverToLabel(confirmation), "Deliver to: Home");
  const bound = orderConfirmationFromEvent({ ...event(), basket: { id: "basket-1", revision: 4 } });
  assert.equal(bound?.basketId, "basket-1");
  assert.equal(bound?.basketRevision, 4);
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
  assert.deepEqual(orderCardStateFromResponse({ ok: true, body: { status: "confirmed", provider_order_id: "ZEP-1" } }), { status: "placed", orderId: "ZEP-1", orderCode: null, tracking: null });
  const tracking = { monitor_id: "m1", order: { order_id: "ZEP-1" } };
  assert.deepEqual(orderCardStateFromResponse({ ok: true, body: { status: "confirmed", provider_order_id: "ZEP-1", tracking } }), { status: "placed", orderId: "ZEP-1", orderCode: null, tracking });
  assert.deepEqual(orderCardStateFromResponse({ ok: true, body: { status: "confirmed", provider_order_id: null } }), { status: "unknown", checkoutId: null });
  assert.deepEqual(orderCardStateFromResponse({ ok: true, body: { status: "outcome_unknown" } }), { status: "unknown", checkoutId: null, check: null, candidates: [] });
  assert.deepEqual(orderCardStateFromResponse({ ok: false, status: 409, message: "This order card expired." }), { status: "failed", reason: "This order card expired." });
  assert.deepEqual(orderCardStateFromResponse({ ok: false, status: 502, message: "Zepto could not refresh this bill." }), { status: "failed", reason: "Zepto could not refresh this bill." });
  // A dropped connection or unexpected server error is never "not placed": the user must not tap again blindly.
  assert.deepEqual(orderCardStateFromResponse({ ok: false, status: 0, message: "" }), { status: "unknown" });
  assert.deepEqual(orderCardStateFromResponse({ ok: false, status: 500, message: "boom" }), { status: "unknown" });
});

test("an unknown outcome offers read-only checks with Zepto and never claims placed", () => {
  const unknown = orderCardStateFromResponse({ ok: true, body: { status: "outcome_unknown", checkout_id: "chk_12345678", check: "not_found" } });
  assert.deepEqual(unknown, { status: "unknown", checkoutId: "chk_12345678", check: "not_found", candidates: [] });
  assert.equal(checkableOrderId(unknown), "chk_12345678");
  assert.equal(checkableOrderId({ status: "unknown" }), null);
  assert.equal(checkableOrderId({ status: "placed", orderId: "o" }), null);
  assert.match(orderCardResultText(unknown, "Zepto"), /hasn't confirmed this order.*Zepto app/);
  assert.doesNotMatch(orderCardResultText(unknown, "Zepto"), /placed/i);
  const ambiguous = orderCardStateFromResponse({ ok: true, body: { status: "outcome_unknown", checkout_id: "chk_12345678", check: "ambiguous", candidates: ["AAA111", "BBB222"] } });
  assert.match(orderCardResultText(ambiguous, "Zepto"), /#AAA111, #BBB222.*won't guess/);
  const settled = orderCardStateFromResponse({ ok: true, body: { status: "confirmed", provider_order_id: "ord-live", provider_order_code: "UIIMJHTCC04881", tracking: null } });
  assert.equal(orderCardResultText(settled, "Zepto"), "Order placed · #UIIMJHTCC04881");
  assert.deepEqual([0, 1, 2, 3].map(orderCheckDelayMs), [20000, 45000, 60000, null]);
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

test("payment options: only known methods, cash on delivery always, chosen one must be offered", () => {
  const card = orderConfirmationFromEvent(event({
    payment_method: "online",
    payment_methods: [{ id: "online", label: "x" }, { id: "card", label: "Card" }, { id: "upi_reserve", label: "UPI" }],
  }));
  assert.equal(card.paymentMethod, "online");
  assert.deepEqual(card.paymentMethods.map((option) => option.id), ["cod", "upi_reserve", "online"]);
  assert.equal(card.paymentMethods.at(-1).label, "Pay online");
  assert.equal(placeButtonLabel(card), "Pay ₹222");
  assert.equal(placeButtonLabel(orderConfirmationFromEvent(event())), "Place order · ₹222");
  assert.equal(orderConfirmationFromEvent(event({ payment_method: "wallet", payment_methods: [{ id: "cod" }] })), null);
});

test("changing the method swaps in the fresh card only when it is valid", () => {
  const card = orderConfirmationFromEvent(event({ payment_methods: [{ id: "cod" }, { id: "online" }] }));
  const fresh = withPaymentMethodCard(card, { id: "conf_newcard12345", expires_at: "2026-11-22T10:06:00Z", payment_method: "online", payment_methods: [{ id: "cod" }, { id: "online" }] });
  assert.equal(fresh.id, "conf_newcard12345");
  assert.equal(fresh.paymentMethod, "online");
  assert.equal(fresh.payable, "₹222");
  assert.equal(withPaymentMethodCard(card, { id: "bad", expires_at: "2026-11-22T10:06:00Z", payment_method: "online" }), null);
  assert.equal(withPaymentMethodCard(card, { id: "conf_newcard12345", expires_at: "2026-11-22T10:06:00Z", payment_method: "wallet", payment_methods: [{ id: "cod" }] }), null);
});

test("prepaid responses map to explicit payment states; only confirmed counts as placed", () => {
  const ok = (body) => orderCardStateFromResponse({ ok: true, body });
  assert.deepEqual(ok({ status: "awaiting_payment", checkout_id: "chk_12345678", payment: { url: "https://pay.example.test/s" } }), { status: "awaiting_payment", checkoutId: "chk_12345678", url: "https://pay.example.test/s" });
  assert.deepEqual(ok({ status: "awaiting_payment", checkout_id: "chk_12345678", payment: { url: "javascript:alert(1)" } }), { status: "awaiting_payment", checkoutId: "chk_12345678", url: null });
  assert.deepEqual(ok({ status: "awaiting_payment" }), { status: "payment_unknown", checkoutId: null });
  assert.deepEqual(ok({ status: "payment_succeeded", checkout_id: "chk_12345678" }), { status: "payment_succeeded", checkoutId: "chk_12345678" });
  assert.deepEqual(ok({ status: "payment_failed", checkout_id: "chk_12345678" }), { status: "payment_failed" });
  assert.deepEqual(ok({ status: "paid_order_failed" }), { status: "paid_order_failed" });
  assert.deepEqual(ok({ status: "payment_unknown", checkout_id: "chk_12345678" }), { status: "payment_unknown", checkoutId: "chk_12345678" });
  assert.deepEqual(ok({ status: "something_new" }), { status: "unknown", checkoutId: null, check: null, candidates: [] });
});

test("only waiting payment states can be checked, on a bounded schedule", () => {
  assert.equal(checkableCheckoutId({ status: "awaiting_payment", checkoutId: "chk_1", url: null }), "chk_1");
  assert.equal(checkableCheckoutId({ status: "payment_succeeded", checkoutId: "chk_1" }), "chk_1");
  assert.equal(checkableCheckoutId({ status: "payment_unknown", checkoutId: null }), null);
  assert.equal(checkableCheckoutId({ status: "payment_failed" }), null);
  assert.equal(checkableCheckoutId({ status: "placed", orderId: "Z" }), null);
  assert.equal(autoCheckDelayMs(0), 15000);
  assert.equal(autoCheckDelayMs(20), null);
});

test("payment links: https pages and upi intents only", () => {
  assert.equal(safePaymentUrl("https://pay.example.test/s?x=1"), "https://pay.example.test/s?x=1");
  assert.equal(safePaymentUrl("upi://pay?pa=m@bank"), "upi://pay?pa=m@bank");
  for (const bad of ["http://pay.example.test", "javascript:alert(1)", "https://u:p@pay.example.test", "https://a b.test", "", null, 3]) assert.equal(safePaymentUrl(bad), null);
});

test("payment result lines are plain English and never claim a placed order", () => {
  for (const state of [{ status: "awaiting_payment", checkoutId: "c", url: null }, { status: "payment_succeeded", checkoutId: "c" }, { status: "payment_unknown", checkoutId: null }, { status: "payment_failed" }, { status: "paid_order_failed" }]) {
    const line = orderCardResultText(state, "Zepto");
    assert.ok(line && !/order placed/i.test(line) && !/PIN|OTP|card number/.test(line), line);
  }
  assert.match(orderCardResultText({ status: "paid_order_failed" }, "Zepto"), /Don't pay again/);
  assert.match(orderCardResultText({ status: "payment_failed" }, "Zepto"), /wasn't placed/);
});
