// Failure modes on the UI side (docs/FAILURE_MODES.md). Pure logic only; rows whose
// behaviour lives inside React components are listed as manual in the catalogue.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { applyAssistantEvent } from "../../app/assistant-events.mjs";
import { cameraErrorKind, cameraErrorMessage, cameraPath, cameraReducer, cameraStatusText } from "../../app/camera-capture.mjs";
import { emptyTimeline, moveBasketCard } from "../../app/chat-timeline.mjs";
import { countdownLabel, orderCardResultText, orderCardStateFromResponse, orderConfirmationFromEvent } from "../../app/order-confirmation.mjs";
import { applyTrackingRead, orderCardFromPlaced } from "../../app/order-status.mjs";
import { uploadErrorMessage, validatePhotoFile } from "../../app/photo-attachment.mjs";
import { zeptoAddressSelection } from "../../app/zepto-address-refresh.mjs";

/**
 * A strict expected failure: passes while `fn` fails, and fails (asking for
 * the marker to be removed) once `fn` passes.
 */
// eslint-disable-next-line @typescript-eslint/no-unused-vars -- for UI rows that fail today; none at the moment
function xfail(id, reason, fn) {
  test(`[xfail ${id}] ${reason}`, async () => {
    let passed = false;
    try {
      await fn();
      passed = true;
    } catch {
      // expected while the row is open
    }
    assert.equal(passed, false, `${id} now passes: remove its xfail (${reason})`);
  });
}

function recorder() {
  const calls = [];
  const handlers = new Proxy({}, { get: (_, name) => (...args) => { calls.push([name, ...args]); } });
  return { calls, handlers };
}

test("FM-P01 a store error is a toast and never an empty results card", () => {
  const { calls, handlers } = recorder();
  const outcome = applyAssistantEvent({ type: "tool.error", query: "lassi", message: "Connected stores could not be searched right now." }, handlers);
  assert.equal(outcome.kind, "error");
  assert.equal(outcome.message, "Connected stores could not be searched right now.");
  assert.deepEqual(calls, []);
  assert.equal(applyAssistantEvent({ type: "tool.error" }, handlers).message, "Something went wrong. Please try again.");
});

test("FM-P06 not connected opens setup, never results", () => {
  const { calls, handlers } = recorder();
  assert.equal(applyAssistantEvent({ type: "tool.not_connected", query: "milk" }, handlers).kind, "not_connected");
  assert.deepEqual(calls, []);
});

test("FM-P10 a malformed results event is ignored, not drawn as an empty card", () => {
  const { calls, handlers } = recorder();
  assert.equal(applyAssistantEvent({ type: "tool.results", query: "milk", products: "oops" }, handlers), null);
  assert.equal(applyAssistantEvent({ type: "tool.results", query: "milk" }, handlers), null);
  assert.equal(applyAssistantEvent("not an event", handlers), null);
  assert.deepEqual(calls, []);
});

test("FM-P02 partial results keep the other store's cards and show the unreachable-store notice", () => {
  const { calls, handlers } = recorder();
  const outcome = applyAssistantEvent({ type: "tool.results", query: "milk", products: [{}], notice: "Zepto isn't responding—try again." }, handlers);
  assert.equal(outcome.notice, "Zepto isn't responding—try again.");
  assert.match(outcome.message, /Zepto isn't responding/);
  assert.equal(calls.length, 1);
});

test("FM-A01 a deleted Zepto address opens the picker and is never silently replaced", () => {
  const { calls, handlers } = recorder();
  const outcome = applyAssistantEvent({ type: "tool.error", message: "Your Zepto saved address changed—choose a delivery address again.", address_removed: "zepto" }, handlers);
  assert.equal(outcome.kind, "error");
  assert.deepEqual(calls, [["onAddressRemoved", "zepto"]]);
  const removed = applyAssistantEvent({ type: "address.selection_removed", provider: "zepto" }, recorder().handlers);
  assert.equal(removed.kind, "address");
  // Even with one address left, the user picks again.
  const left = { addresses: [{ id: "office" }], resolution: { default_address_id: null } };
  assert.deepEqual(zeptoAddressSelection({ ...left, selection_removed: true }, "home"), { selectedId: "", selectionRemoved: true });
  assert.deepEqual(zeptoAddressSelection(left, "home"), { selectedId: "", selectionRemoved: true });
});

test("FM-O12 only a provider-confirmed order id shows 'placed'; anything doubtful is 'unknown'", () => {
  assert.deepEqual(orderCardStateFromResponse({ ok: true, body: { status: "confirmed", provider_order_id: "ZP-1" } }), { status: "placed", orderId: "ZP-1", orderCode: null, tracking: null });
  for (const body of [{ status: "confirmed" }, { status: "outcome_unknown" }, { status: "something_new" }, null]) {
    assert.equal(orderCardStateFromResponse({ ok: true, body }).status, "unknown");
  }
  // Dropped connection, server crash, proxy timeout: the order may exist, never tap again.
  for (const status of [0, 500, 504]) assert.equal(orderCardStateFromResponse({ ok: false, status, message: "x" }).status, "unknown");
  // 502/503 are raised before dispatch (bill refresh failed): nothing was ordered.
  assert.equal(orderCardStateFromResponse({ ok: false, status: 502, message: "Zepto could not refresh this bill. No order was placed." }).status, "failed");
  assert.equal(orderCardStateFromResponse({ ok: false, status: 409, message: "This order card was already used." }).status, "failed");
});

test("FM-O01 the unknown card never says placed and tells the user to check first", () => {
  const text = orderCardResultText({ status: "unknown" }, "Zepto");
  assert.doesNotMatch(text, /placed ·|Order placed/);
  assert.match(text, /hasn't confirmed/);
  assert.match(orderCardResultText({ status: "payment_unknown", checkoutId: "c" }, "Zepto"), /Don't pay again/);
  assert.match(orderCardResultText({ status: "paid_order_failed" }, "Zepto"), /Don't pay again/);
  assert.match(orderCardResultText({ status: "payment_failed" }, "Zepto"), /wasn't placed/);
});

test("FM-O05 a prepaid response without a checkout id is payment_unknown, never awaiting a link", () => {
  assert.deepEqual(orderCardStateFromResponse({ ok: true, body: { status: "awaiting_payment" } }), { status: "payment_unknown", checkoutId: null });
  const state = orderCardStateFromResponse({ ok: true, body: { status: "awaiting_payment", checkout_id: "chk_123456789", payment: { url: "javascript:alert(1)" } } });
  assert.equal(state.url, null);
});

test("FM-O10 an expired card has no countdown, and a malformed card is never shown", () => {
  assert.equal(countdownLabel("2026-11-22T10:00:00Z", Date.parse("2026-11-22T10:00:01Z")), null);
  assert.equal(orderConfirmationFromEvent({ type: "order.confirmation_requested", provider: "zepto", confirmation: { id: "bad" } }), null);
});

test("FM-B06 a basket change moves the live basket card and leaves a summary behind", () => {
  let timeline = moveBasketCard(emptyTimeline(), { provider: "zepto", afterMessageId: "m1", previous: null });
  timeline = moveBasketCard(timeline, { provider: "zepto", afterMessageId: "m2", previous: { stage: "bill_ready", itemCount: 2, itemTotalPaise: 19700 } });
  assert.equal(timeline.live.length, 1);
  assert.equal(timeline.live[0].afterMessageId, "m2");
  assert.equal(timeline.summaries.length, 1);
  assert.equal(timeline.summaries[0].stage, "bill_ready");
});

test("FM-T01 a tracking read for another order or a broken read leaves the card alone", () => {
  const card = orderCardFromPlaced({ orderId: "ZP-1", store: "Zepto", afterMessageId: null, seq: 1 });
  assert.equal(applyTrackingRead(card, { order: { order_id: "ZP-2", status: "delivered" } }).card, card);
  assert.equal(applyTrackingRead(card, null).card, card);
  // Tracking stopped on a provider error: the last store-reported step stays.
  const out = applyTrackingRead(card, { order: { order_id: "ZP-1", status: "unavailable", snapshot: { status: "out_for_delivery" } } });
  assert.equal(out.card.status, "out_for_delivery");
});

test("FM-PH01 photo upload failures have a specific message per status", () => {
  assert.match(uploadErrorMessage(413), /too large/);
  assert.match(uploadErrorMessage(415), /JPEG, PNG, WebP or HEIC/);
  assert.match(uploadErrorMessage(422), /couldn't be read/);
  assert.match(uploadErrorMessage(403), /Accept the terms/);
  assert.match(uploadErrorMessage(401), /sign in/);
  assert.match(uploadErrorMessage(0), /couldn't be uploaded/);
  assert.match(uploadErrorMessage(500), /couldn't be uploaded/);
  assert.equal(validatePhotoFile(null), "No photo was chosen.");
  assert.match(validatePhotoFile({ type: "image/svg+xml", size: 10, name: "x.svg" }), /JPEG/);
});

test("FM-PH05 webcam denied, missing or busy each get a way out to the file picker", () => {
  assert.equal(cameraErrorKind({ name: "NotAllowedError" }), "denied");
  assert.equal(cameraErrorKind({ name: "SecurityError" }), "denied");
  assert.equal(cameraErrorKind({ name: "NotFoundError" }), "not_found");
  assert.equal(cameraErrorKind({ name: "OverconstrainedError" }), "not_found");
  assert.equal(cameraErrorKind({ name: "NotReadableError" }), "in_use");
  assert.equal(cameraErrorKind(new TypeError("navigator.mediaDevices is undefined")), "unknown");
  assert.equal(cameraErrorKind(undefined), "unknown");
  for (const kind of ["denied", "not_found", "in_use", "unknown"]) assert.match(cameraErrorMessage(kind), /choose a photo from your files/);
  const failed = cameraReducer(cameraReducer({ status: "idle" }, { type: "start", prompt: true }), { type: "failed", kind: "denied" });
  assert.deepEqual(failed, { status: "error", kind: "denied" });
  assert.match(cameraStatusText(failed), /Allow camera access/);
  // A late failure after capture does not throw away the photo.
  assert.deepEqual(cameraReducer({ status: "captured" }, { type: "failed", kind: "in_use" }), { status: "captured" });
});

test("FM-PH05 / FM-V04 without getUserMedia (plain http on a phone) the camera falls back to the native picker", () => {
  assert.equal(cameraPath({ userAgent: "Mozilla/5.0 (Macintosh)", hasGetUserMedia: false }), "native");
  assert.equal(cameraPath({ userAgent: "Mozilla/5.0 (Macintosh)", hasGetUserMedia: true }), "webcam");
});

test("FM-V07 browser-extension attributes on <html>/<body> do not break hydration", () => {
  const layout = readFileSync(new URL("../../app/layout.tsx", import.meta.url), "utf8");
  assert.match(layout, /<html[^>]*suppressHydrationWarning/);
  assert.match(layout, /<body[^>]*suppressHydrationWarning/);
});
