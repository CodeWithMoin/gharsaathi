// Clock strings below are India time; the card formats in the device's own zone.
process.env.TZ = "Asia/Kolkata";

import assert from "node:assert/strict";
import test from "node:test";

import { emptyTimeline, placeTimelineItems, recordOrderCard, restoreTimeline, timelineSnapshot, updateOrderCard } from "../../app/chat-timeline.mjs";
import { applyTrackingRead, deliveredText, etaText, finishedOrderSummary, orderCardFromPlaced, orderCardSnapshot, placedText, restoreOrderCard, orderHeadline, orderSteps, pollDelayMs, updatedText } from "../../app/order-status.mjs";

const NOW = Date.parse("2026-11-22T18:00:00Z");
const at = (minutes) => new Date(NOW + minutes * 60_000).toISOString();

/** A backend TrackingRead as GET/POST /api/tracking/monitors/{id} returns it. */
function read({ status = "confirmed", active = true, eta = null, label = null, notifications = [], next = 1, snapshot = {} } = {}) {
  return {
    monitor_id: "m-1",
    next_check_at: active ? at(next) : null,
    order: {
      id: "m-1",
      order_id: "ZEP-9",
      provider: "zepto",
      active,
      status,
      status_at: at(0),
      snapshot: { status, provider_status: label, eta_at: eta === null ? null : at(eta), delivered: status === "delivered", cancelled: status === "cancelled", ...snapshot },
      notifications,
    },
  };
}

const base = () => orderCardFromPlaced({ orderId: "ZEP-9", store: "Zepto", afterMessageId: "a1", seq: 0, itemCount: 2, payable: "₹222", tracking: read({ eta: 15 }) });
const states = (status) => orderSteps(status).map((step) => `${step.label}:${step.state}`);

test("steps map placed → packed → out for delivery → delivered, and cancelled ends the row", () => {
  assert.deepEqual(states("confirmed"), ["Placed:current", "Packed:todo", "Out for delivery:todo", "Delivered:todo"]);
  assert.deepEqual(states("preparing"), ["Placed:done", "Packed:current", "Out for delivery:todo", "Delivered:todo"]);
  assert.deepEqual(states("out_for_delivery"), ["Placed:done", "Packed:done", "Out for delivery:current", "Delivered:todo"]);
  assert.deepEqual(states("delivered"), ["Placed:done", "Packed:done", "Out for delivery:done", "Delivered:done"]);
  assert.deepEqual(states("cancelled"), ["Placed:done", "Packed:todo", "Out for delivery:todo", "Cancelled:cancelled"]);
  // A status we don't know is never guessed into a step.
  assert.deepEqual(states("unknown"), ["Placed:done", "Packed:todo", "Out for delivery:todo", "Delivered:todo"]);
});

test("the headline uses the store's own words for an unmapped status", () => {
  assert.equal(orderHeadline({ status: "out_for_delivery", providerStatus: "On the way" }), "Out for delivery");
  assert.equal(orderHeadline({ status: "unknown", providerStatus: "Rider assigned" }), "Rider assigned");
  assert.equal(orderHeadline({ status: "unknown", providerStatus: null }), "Order placed");
});

test("a placed order starts from the confirm response's monitor", () => {
  const card = base();
  assert.equal(card.monitorId, "m-1");
  assert.equal(card.status, "confirmed");
  assert.equal(card.etaAt, at(15));
  assert.equal(card.active, true);
  const untracked = orderCardFromPlaced({ orderId: "ZEP-9", store: "Zepto", afterMessageId: null, seq: 0 });
  assert.equal(untracked.monitorId, null);
  assert.equal(untracked.active, false);
});

test("reads move the card forward; only real changes are saved; each update toasts once", () => {
  let card = base();
  const packed = applyTrackingRead(card, read({ status: "preparing", eta: 15 }));
  assert.equal(packed.changed, true);
  assert.equal(packed.toast, null);
  card = packed.card;

  const same = applyTrackingRead(card, read({ status: "preparing", eta: 15 }));
  assert.equal(same.changed, false);
  assert.equal(same.toast, null);

  const notification = { id: "n1", kind: "out_for_delivery", message: "Your Zepto order is out for delivery." };
  const out = applyTrackingRead(card, read({ status: "out_for_delivery", eta: 6, notifications: [notification] }));
  assert.equal(out.toast, "Your Zepto order is out for delivery.");
  assert.equal(out.card.lastNotificationId, "n1");
  card = out.card;
  assert.equal(applyTrackingRead(card, read({ status: "out_for_delivery", eta: 6, notifications: [notification] })).toast, null);

  const delivered = applyTrackingRead(card, read({ status: "delivered", active: false, eta: 6, notifications: [notification, { id: "n2", kind: "delivered", message: "Your Zepto order was delivered." }] }));
  assert.equal(delivered.toast, "Your Zepto order was delivered.");
  assert.equal(delivered.card.status, "delivered");
  assert.equal(delivered.card.etaAt, null);
  assert.equal(delivered.card.active, false);
});

test("a stopped monitor keeps the last store-reported step and a read for another order is ignored", () => {
  const card = applyTrackingRead(base(), read({ status: "preparing" })).card;
  const paused = applyTrackingRead(card, read({ status: "paused", active: false, snapshot: { status: "preparing" } }));
  assert.equal(paused.card.status, "preparing");
  assert.equal(paused.card.active, false);
  const other = read({ status: "delivered" });
  other.order.order_id = "ZEP-OTHER";
  assert.equal(applyTrackingRead(card, other).card, card);
});

test("polling follows the backend's allowed time and stops when tracking stops", () => {
  assert.equal(pollDelayMs(read({ next: 1.5 }), NOW), 91_000);
  assert.equal(pollDelayMs(read({ next: -3 }), NOW), 5_000);
  assert.equal(pollDelayMs(read({ next: 60 }), NOW), 5 * 60_000);
  assert.equal(pollDelayMs(read({ active: false }), NOW), null);
  assert.equal(pollDelayMs({}, NOW), null);
});

test("ETA and last-updated text come only from the store's numbers", () => {
  // NOW is 11:30 pm India time.
  assert.equal(etaText({ etaAt: at(6), status: "out_for_delivery", store: "Zepto" }, NOW), "Arriving by 11:36 pm · about 6 min");
  assert.equal(etaText({ etaAt: at(-2), status: "out_for_delivery", store: "Zepto" }, NOW), "Running late — Zepto estimated 11:28 pm");
  assert.equal(etaText({ etaAt: null, status: "out_for_delivery", store: "Zepto" }, NOW), null);
  assert.equal(etaText({ etaAt: at(6), status: "delivered", store: "Zepto" }, NOW), null);
  assert.equal(updatedText(at(0), NOW + 20_000), "Updated just now");
  assert.equal(updatedText(at(0), NOW + 4 * 60_000), "Updated 4 min ago");
  assert.match(updatedText(at(0), NOW + 90 * 60_000), /^Updated at /);
  assert.equal(updatedText(null, NOW), null);
});

test("the status card stays where the order was placed and survives a reload", () => {
  const messages = [{ id: "u1" }, { id: "a1" }, { id: "u2" }];
  let timeline = recordOrderCard(emptyTimeline(), base());
  timeline = updateOrderCard(timeline, "ZEP-9", applyTrackingRead(timeline.orders[0], read({ status: "out_for_delivery", eta: 6 })).card);
  const layout = (state) => placeTimelineItems({ messages, timeline: state }).map((item) => item.kind === "message" ? item.message.id : `${item.kind}:${item.card.orderId}`);
  assert.deepEqual(layout(timeline), ["u1", "a1", "order-status:ZEP-9", "u2"]);

  const restored = restoreTimeline(JSON.parse(JSON.stringify(timelineSnapshot(timeline))));
  assert.deepEqual(restored.orders, timeline.orders);
  assert.deepEqual(layout(restored), layout(timeline));
  // Recording the same order again updates it in place instead of adding a card.
  assert.equal(recordOrderCard(restored, base()).orders.length, 1);
  // Chats without orders keep their old snapshot shape.
  assert.equal("orders" in timelineSnapshot(emptyTimeline()), false);
});

// The owner's real delivered order UIIMJHTCC04881, as the backend reports it.
const PLACED = "2026-11-22T02:48:10Z";
const realRead = (status, snapshot = {}) => ({
  monitor_id: "m-1",
  next_check_at: null,
  order: {
    id: "m-1",
    order_id: "ZEP-9",
    active: status !== "delivered",
    status,
    status_at: status === "delivered" ? "2026-11-22T02:56:59Z" : "2026-11-22T02:52:00Z",
    checked_at: "2026-11-22T03:28:00Z",
    snapshot: { status, delivered: status === "delivered", placed_at: PLACED, committed_eta_minutes: 8, ...snapshot },
    notifications: [],
  },
});

test("the countdown counts down each minute to Zepto's time, then says it is running late", () => {
  const card = applyTrackingRead(base(), realRead("out_for_delivery", { eta_at: "2026-11-22T02:56:10Z" })).card;
  const start = Date.parse("2026-11-22T02:52:10Z");
  assert.equal(etaText(card, start), "Arriving by 8:26 am · about 4 min");
  assert.equal(etaText(card, start + 60_000), "Arriving by 8:26 am · about 3 min");
  assert.equal(etaText(card, start + 3 * 60_000 + 30_000), "Arriving by 8:26 am · about 1 min");
  assert.equal(etaText(card, start + 4 * 60_000), "Running late — Zepto estimated 8:26 am");
  assert.equal(placedText(card), "Placed at 8:18 am");
  assert.equal(deliveredText(card), null);
  assert.equal(finishedOrderSummary(card), null);
});

test("a delivered order shows Zepto's delivered time, how long it took and the estimate", () => {
  const moving = applyTrackingRead(base(), realRead("out_for_delivery", { eta_at: "2026-11-22T02:56:10Z" })).card;
  const delivered = applyTrackingRead(moving, realRead("delivered", { delivered_at: "2026-11-22T02:56:59Z", took_minutes: 9 }));
  assert.equal(delivered.changed, true);
  assert.equal(delivered.card.finishedAt, "2026-11-22T02:56:59Z");
  assert.equal(finishedOrderSummary(delivered.card), "Delivered ✓ · 8:26 am · took 9 min (estimate 8 min)");
  assert.equal(deliveredText(delivered.card), "8:26 am · took 9 min (estimate 8 min)");
  assert.equal(etaText(delivered.card, Date.parse("2026-11-22T03:28:00Z")), null);
  // Saved with the chat and restored as is.
  const restored = restoreOrderCard(JSON.parse(JSON.stringify(orderCardSnapshot(delivered.card))));
  assert.equal(finishedOrderSummary(restored), "Delivered ✓ · 8:26 am · took 9 min (estimate 8 min)");
});

test("times the store did not report are left out, never guessed", () => {
  const bare = applyTrackingRead(base(), realRead("delivered", { placed_at: null, committed_eta_minutes: null })).card;
  // No delivered_at: falls back to the monitor's status time (here Zepto's, 8:26 am).
  assert.equal(finishedOrderSummary(bare), "Delivered ✓ · 8:26 am");
  assert.equal(placedText(bare), null);
  const noTimes = { ...bare, finishedAt: null };
  assert.equal(finishedOrderSummary(noTimes), "Delivered ✓");
  assert.equal(deliveredText({ ...noTimes, tookMinutes: 9, estimateMinutes: null }), "took 9 min");
  // A saved card from before these fields existed still restores.
  const old = restoreOrderCard({ provider: "zepto", order_id: "ZEP-9", seq: 0, status: "delivered", finished_at: "2026-11-22T03:28:00Z" });
  assert.equal(old.placedAt, null);
  assert.equal(old.tookMinutes, null);
  assert.equal(finishedOrderSummary(old), "Delivered ✓ · 8:58 am");
});
