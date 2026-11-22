import assert from "node:assert/strict";
import test from "node:test";
import {
  STALE_BILL_TEXT,
  billIsStale,
  cartBadgeCount,
  newOrderCard,
  orderCardInteractive,
  orderCardKey,
  orderedSummary,
  supersedeOrderCard,
  supersededOrderText,
  withOrderResult,
} from "../../app/card-lifecycle.mjs";
import { collapseBasketCard, emptyTimeline, moveBasketCard, placeTimelineItems, recordOrderCard, restoreTimeline, summaryText, timelineSnapshot } from "../../app/chat-timeline.mjs";
import { orderConfirmationFromEvent } from "../../app/order-confirmation.mjs";
import { applyTrackingRead, finishedOrderSummary, orderCardFromPlaced } from "../../app/order-status.mjs";

const LASSI = "Amul Sweet Lassi";

/** The store's basket at one revision. */
const basket = (revision, quantity, { id = "basket-1", status = "draft" } = {}) => ({
  id,
  provider: "zepto",
  revision,
  status,
  lines: quantity ? [{ quantity }] : [],
});

/** A bill (`POST /api/basket/approval` shape) priced for one basket revision. */
const bill = (revision, payable, id = "basket-1") => ({ basket: { id, revision }, quote: { payable: { display: payable } } });

let cardIds = 0;
/** An `order.confirmation_requested` event for one basket revision. */
const orderEvent = (revision, quantity, payable, paise) => orderConfirmationFromEvent({
  type: "order.confirmation_requested",
  provider: "zepto",
  store: "Zepto",
  confirmation: {
    id: `confirmation_${String(++cardIds).padStart(4, "0")}`,
    expires_at: "2026-11-22T10:05:00Z",
    payable,
    payable_paise: paise,
    item_count: quantity,
    items: [{ name: LASSI, pack_size: "200 ml", quantity }],
  },
  basket: { id: "basket-1", revision },
});

/**
 * The page's card state, driven exactly like page.tsx: every basket read goes
 * through supersedeOrderCard, the basket panel hides a bill once billIsStale,
 * and a new order card replaces the old one.
 */
function page() {
  const state = { basket: null, bill: null, billOutdated: false, orderCard: null };
  return {
    state,
    basketRead(next) {
      state.basket = next;
      state.orderCard = supersedeOrderCard(state.orderCard, next);
      if (state.bill && billIsStale(next, state.bill)) {
        state.bill = null;
        state.billOutdated = true;
      }
    },
    billShown(next) {
      state.bill = next;
      state.billOutdated = false;
    },
    orderCardShown(confirmation, anchor = "m-latest") {
      state.orderCard = newOrderCard(confirmation, anchor);
    },
    visible() {
      const card = state.orderCard;
      return {
        bill: state.bill?.quote.payable.display ?? (state.billOutdated ? STALE_BILL_TEXT : null),
        orderCard: card ? (orderCardInteractive(card) ? `${card.confirmation.itemCount} × ${card.confirmation.payable}` : supersededOrderText(card)) : null,
        interactive: card ? orderCardInteractive(card) : false,
      };
    },
  };
}

test("the owner's session: only the latest bill and order card values are visible and tappable", () => {
  const ui = page();
  // 2 lassi → the bill fails (nothing shown).
  ui.basketRead(basket(1, 2));
  assert.deepEqual(ui.visible(), { bill: null, orderCard: null, interactive: false });
  // Remove, then re-add one: 1 lassi, bill ₹55.
  ui.basketRead(basket(2, 0));
  ui.basketRead(basket(3, 1));
  ui.billShown(bill(3, "₹55"));
  assert.equal(ui.visible().bill, "₹55");
  // Add one more: the ₹55 bill is out of date at once.
  ui.basketRead(basket(4, 2));
  assert.equal(ui.visible().bill, STALE_BILL_TEXT);
  // A fresh bill, then the order card: 2 items, ₹75.
  ui.billShown(bill(4, "₹75"));
  const first = orderEvent(4, 2, "₹75", 7500);
  ui.orderCardShown(first);
  assert.deepEqual(ui.visible(), { bill: "₹75", orderCard: "2 × ₹75", interactive: true });
  // Change to 1: the ₹75 bill and order card are out of date immediately, no buttons.
  ui.basketRead(basket(5, 1));
  assert.deepEqual(ui.visible(), {
    bill: STALE_BILL_TEXT,
    orderCard: "Zepto order · 2 items · ₹75 · Out of date — basket changed",
    interactive: false,
  });
  // The new card replaces the old one with the new values.
  ui.billShown(bill(5, "₹55"));
  const second = orderEvent(5, 1, "₹55", 5500);
  ui.orderCardShown(second);
  assert.deepEqual(ui.visible(), { bill: "₹55", orderCard: "1 × ₹55", interactive: true });
  assert.notEqual(orderCardKey(newOrderCard(first, null)), orderCardKey(ui.state.orderCard));
  assert.match(orderCardKey(ui.state.orderCard), /basket-1-5-confirmation_/);
});

test("a late older basket read never supersedes a newer card", () => {
  const card = newOrderCard(orderEvent(5, 1, "₹55", 5500), null);
  assert.equal(supersedeOrderCard(card, basket(4, 2)), card);
  assert.equal(supersedeOrderCard(card, basket(5, 1, { status: "awaiting_approval" })), card);
  assert.equal(supersedeOrderCard(card, { ...basket(5, 1), provider: "swiggy_instamart" }), card);
  assert.equal(supersedeOrderCard(card, basket(6, 1)).superseded, true);
  // An ordered basket is replaced by a new one: an open card for it is out of date.
  assert.equal(supersedeOrderCard(card, basket(0, 0, { id: "basket-2" })).superseded, true);
});

test("a card whose order may exist is never collapsed by a basket change", () => {
  const base = newOrderCard(orderEvent(5, 1, "₹55", 5500), null);
  for (const result of ["placing", "unknown", "checking_order", "awaiting_payment", "payment_unknown", "placed"]) {
    const card = withOrderResult(base, base.confirmation.id, result);
    assert.equal(supersedeOrderCard(card, basket(6, 2)).superseded, false, result);
  }
  for (const result of ["failed", "cancelled", "expired"]) {
    const card = withOrderResult(base, base.confirmation.id, result);
    assert.equal(supersedeOrderCard(card, basket(6, 2)).superseded, true, result);
    assert.equal(orderCardInteractive(card), false);
  }
  assert.equal(withOrderResult(base, "someone-else", "failed"), base);
});

test("bills are stale only for another basket or revision", () => {
  assert.equal(billIsStale(basket(3, 1), bill(3, "₹55")), false);
  assert.equal(billIsStale(basket(4, 2), bill(3, "₹55")), true);
  assert.equal(billIsStale(basket(0, 0, { id: "basket-2" }), bill(3, "₹55")), true);
  assert.equal(billIsStale(null, bill(3, "₹55")), false);
  assert.equal(billIsStale(basket(3, 1), null), false);
});

test("a placed order empties the cart badge and leaves an 'Ordered' summary linked to its status card", () => {
  const open = { zepto: basket(5, 1, { status: "approved" }), swiggy_instamart: { ...basket(2, 3), id: "s", provider: "swiggy_instamart" } };
  assert.equal(cartBadgeCount(open), 4);
  assert.equal(cartBadgeCount({ ...open, zepto: { ...open.zepto, status: "ordered" } }), 3);
  assert.equal(cartBadgeCount({ zepto: { ...open.zepto, status: "checkout_pending" } }), 1); // unknown outcome: still locked, still shown

  let timeline = moveBasketCard(emptyTimeline(), { provider: "zepto", afterMessageId: "m1", previous: null });
  const summary = orderedSummary({ itemCount: 1, payablePaise: 5500, orderId: "ord-live" });
  timeline = recordOrderCard(collapseBasketCard(timeline, "zepto", summary), orderCardFromPlaced({ orderId: "ord-live", store: "Zepto", afterMessageId: "m1", seq: 0, itemCount: 1, payable: "₹55", itemNames: [LASSI] }));
  const items = placeTimelineItems({ messages: [{ id: "m1" }], timeline });
  assert.deepEqual(items.map((item) => item.kind), ["message", "basket-summary", "order-status"]);
  assert.equal(summaryText(items[1].card), "Ordered · 1 item · ₹55");
  assert.equal(items[1].card.orderId, "ord-live");
  // No live basket card is left; the next add opens a new one.
  assert.deepEqual(timeline.live, []);
  // The link survives a reload.
  const restored = restoreTimeline(JSON.parse(JSON.stringify(timelineSnapshot(timeline))));
  assert.equal(restored.summaries[0].orderId, "ord-live");
  assert.equal(restored.orders[0].itemNames[0], LASSI);
});

const read = (status, extra = {}) => ({
  monitor_id: "mon-1",
  order: { id: "mon-1", order_id: "ord-live", status, active: status !== "delivered" && status !== "cancelled", status_at: "2026-11-22T03:11:00Z", snapshot: { status, provider_status: status, delivered: status === "delivered", cancelled: status === "cancelled" }, notifications: [], ...extra },
});

test("a delivered or store-cancelled order collapses to one line, and the line survives a reload", () => {
  const placed = orderCardFromPlaced({ orderId: "ord-live", store: "Zepto", afterMessageId: "m1", seq: 1, itemCount: 1, payable: "₹55", itemNames: [LASSI], tracking: read("preparing") });
  assert.equal(finishedOrderSummary(placed), null);

  const delivered = applyTrackingRead(placed, read("delivered")).card;
  const line = finishedOrderSummary(delivered);
  // Items and total move to the expanded card; the line is the delivered time.
  assert.match(line, /^Delivered ✓ · \d{1,2}:\d{2}/);
  let timeline = recordOrderCard(emptyTimeline(), delivered);
  const restored = restoreTimeline(JSON.parse(JSON.stringify(timelineSnapshot(timeline))));
  assert.equal(finishedOrderSummary(restored.orders[0]), line);

  const cancelled = applyTrackingRead(placed, read("cancelled")).card;
  assert.match(finishedOrderSummary(cancelled), /^Cancelled by Zepto · Amul Sweet Lassi · ₹55/);
  const many = { ...cancelled, itemNames: [LASSI, "Amul Butter", "Bread"] };
  assert.match(finishedOrderSummary(many), /Amul Sweet Lassi \+2 more/);
  timeline = recordOrderCard(timeline, cancelled);
  assert.equal(timeline.orders.length, 1); // same order: replaced in place
});

test("an assistant basket edit collapses the open order card at once, without a basket re-read or a tap", async () => {
  const { applyAssistantEvent } = await import("../../app/assistant-events.mjs");
  // page.tsx's handleBasketTouched: the event's basket supersedes the card; no BasketPanel is mounted.
  let card = newOrderCard(orderEvent(4, 2, "₹75", 7500), "m1");
  const touched = [];
  const handlers = {
    onBasketTouched(provider, next) {
      touched.push(provider);
      if (next) card = supersedeOrderCard(card, next);
    },
  };
  const updated = (revision, quantity, extra = {}) => ({
    type: "basket.updated",
    provider: "zepto",
    basket: { id: "basket-1", provider: "zepto", store: "Zepto", revision, status: "draft", item_count: quantity, item_total: "₹55", lines: [{ item_id: "v1", name: LASSI, quantity }], ...extra },
  });

  // The same revision (the edit the card was prepared from) leaves it tappable.
  applyAssistantEvent(updated(4, 2), handlers);
  assert.equal(orderCardInteractive(card), true);
  // "Ek hi kar do": the basket moves to revision 5 and the ₹75 card is out of date now.
  applyAssistantEvent(updated(5, 1), handlers);
  assert.equal(card.superseded, true);
  assert.equal(orderCardInteractive(card), false);
  assert.equal(supersededOrderText(card), "Zepto order · 2 items · ₹75 · Out of date — basket changed");
  assert.deepEqual(touched, ["zepto", "zepto"]);
});

test("an assistant basket edit never collapses a card whose order may exist", async () => {
  const { applyAssistantEvent } = await import("../../app/assistant-events.mjs");
  for (const result of ["placing", "unknown", "awaiting_payment", "checking", "payment_unknown"]) {
    const base = newOrderCard(orderEvent(4, 2, "₹75", 7500), null);
    let card = withOrderResult(base, base.confirmation.id, result);
    applyAssistantEvent(
      { type: "basket.updated", provider: "zepto", basket: { id: "basket-1", provider: "zepto", revision: 9, status: "draft", lines: [] } },
      { onBasketTouched: (_provider, next) => { if (next) card = supersedeOrderCard(card, next); } },
    );
    assert.equal(card.superseded, false, result);
  }
});

test("basketFromEvent reads only a well-formed basket for the event's store", async () => {
  const { basketFromEvent } = await import("../../app/card-lifecycle.mjs");
  assert.deepEqual(basketFromEvent({ id: "b", provider: "zepto", revision: 2, status: "draft", lines: [{ quantity: 1 }] }, "zepto"), { id: "b", provider: "zepto", revision: 2, status: "draft", lines: [{ quantity: 1 }] });
  assert.equal(basketFromEvent({ id: "b", provider: "zepto", revision: 2 }, "swiggy_instamart"), null);
  assert.equal(basketFromEvent({ id: "b", provider: "zepto", revision: "2" }, "zepto"), null);
  assert.equal(basketFromEvent({ provider: "zepto", revision: 2 }, "zepto"), null);
  assert.equal(basketFromEvent(null, "zepto"), null);
  assert.equal(basketFromEvent([], "zepto"), null);
});
