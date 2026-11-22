import assert from "node:assert/strict";
import test from "node:test";
import { CLOSED_SHEET, EMPTY_TITLE, FEES_NOTE, afterBill, billAvailability, emptyStateChips, pickTab, sheetReducer, sheetStores, sheetView, shouldDismiss } from "../../app/cart-sheet-state.mjs";
import { orderCardInFlight } from "../../app/card-lifecycle.mjs";

const basket = (provider, quantities, extra = {}) => ({ provider, status: "draft", address_id: "addr-1", lines: quantities.map((quantity) => ({ quantity })), ...extra });
const both = { zepto: basket("zepto", [2, 1]), swiggy_instamart: basket("swiggy_instamart", [1]) };

test("opening picks the preferred store's tab; toggling and close shut it", () => {
  const opened = sheetReducer(CLOSED_SHEET, { type: "open", baskets: both, preferred: "swiggy_instamart" });
  assert.deepEqual(opened, { open: true, tab: "swiggy_instamart" });
  assert.equal(sheetReducer(opened, { type: "toggle", baskets: both }).open, false);
  assert.equal(sheetReducer(opened, { type: "close" }).open, false);
  assert.equal(sheetReducer(CLOSED_SHEET, { type: "close" }), CLOSED_SHEET);
  assert.deepEqual(sheetReducer(CLOSED_SHEET, { type: "toggle", baskets: both, preferred: "zepto" }), { open: true, tab: "zepto" });
});

test("preferred store without items falls back to the store that has them", () => {
  const onlyZepto = { zepto: basket("zepto", [1]), swiggy_instamart: basket("swiggy_instamart", []) };
  assert.equal(pickTab(onlyZepto, null, "swiggy_instamart"), "zepto");
  assert.equal(sheetReducer(CLOSED_SHEET, { type: "open", baskets: onlyZepto, preferred: "swiggy_instamart" }).tab, "zepto");
});

test("store tabs show only when both stores have items, Zepto first with counts", () => {
  assert.deepEqual(sheetStores(both), [{ provider: "zepto", label: "Zepto", count: 3 }, { provider: "swiggy_instamart", label: "Instamart", count: 1 }]);
  assert.equal(sheetView(both, "zepto").showTabs, true);
  assert.equal(sheetView({ zepto: both.zepto }, "zepto").showTabs, false);
  assert.equal(sheetView(both, "zepto").totalCount, 4);
});

test("selecting a tab works only for a store with items; an emptied tab moves to the other store", () => {
  const state = { open: true, tab: "zepto" };
  assert.equal(sheetReducer(state, { type: "select", provider: "swiggy_instamart", baskets: both }).tab, "swiggy_instamart");
  assert.equal(sheetReducer(state, { type: "select", provider: "swiggy_instamart", baskets: { zepto: both.zepto } }), state);
  assert.equal(sheetView({ zepto: basket("zepto", []), swiggy_instamart: both.swiggy_instamart }, "zepto").tab, "swiggy_instamart");
});

test("empty state: no items anywhere (an ordered basket counts as empty) shows recent-item chips only", () => {
  const view = sheetView({ zepto: basket("zepto", [2], { status: "ordered" }) }, "zepto");
  assert.equal(view.empty, true);
  assert.equal(view.tab, null);
  assert.equal(EMPTY_TITLE, "Your basket is empty");
  const chips = [{ key: "repeat", action: { kind: "repeat" } }, { key: "recent-a", action: { kind: "recent" } }, { key: "lists", action: { kind: "lists" } }];
  assert.deepEqual(emptyStateChips(chips).map((chip) => chip.key), ["recent-a"]);
  assert.equal(FEES_NOTE, "Fees and final total on the bill");
});

test("Get final bill: enabled when ready, otherwise disabled with a plain reason", () => {
  const ready = { basket: both.zepto, connected: true, addressId: "addr-1" };
  assert.deepEqual(billAvailability(ready), { disabled: false, reason: null });
  assert.deepEqual(billAvailability({ ...ready, busy: true }), { disabled: true, reason: null });
  assert.match(billAvailability({ ...ready, basket: { ...both.zepto, status: "checkout_pending" } }).reason, /Zepto is still confirming an order/);
  assert.match(billAvailability({ ...ready, orderInFlight: true }).reason, /don't know yet if your last Zepto order went through/);
  assert.match(billAvailability({ ...ready, addressId: "" }).reason, /Choose a Zepto delivery address/);
  assert.match(billAvailability({ ...ready, connected: false, addressId: "" }).reason, /Zepto isn't connected/);
  assert.equal(billAvailability({ ...ready, basket: { ...both.zepto, status: "ordered" } }).reason, "This basket was ordered.");
  assert.equal(billAvailability({ ...ready, basket: basket("zepto", []) }).disabled, true);
  assert.equal(billAvailability({ ...ready, basket: null }).disabled, true);
});

test("a ready bill closes the sheet and emits the bill card for the chat timeline", () => {
  const request = { approval: { id: "a1" } };
  const { state, emit } = afterBill({ open: true, tab: "zepto" }, "zepto", request);
  assert.equal(state.open, false);
  assert.deepEqual(emit, { kind: "bill", provider: "zepto", stage: "bill_ready", request });
});

test("drag down: a flick or a long drag closes; a short slow drag or a flick up stays", () => {
  assert.equal(shouldDismiss({ offset: 40, velocity: 900, height: 600 }), true);
  assert.equal(shouldDismiss({ offset: 320, velocity: 0, height: 600 }), true);
  assert.equal(shouldDismiss({ offset: 60, velocity: 50, height: 600 }), false);
  assert.equal(shouldDismiss({ offset: 320, velocity: -800, height: 600 }), false);
  assert.equal(shouldDismiss({ offset: 100, velocity: 0, height: 0 }), false);
});

test("an order card still going through blocks a new bill; open, placed or superseded cards don't", () => {
  const card = (result, superseded = false) => ({ confirmation: { provider: "zepto" }, afterMessageId: null, result, superseded });
  assert.equal(orderCardInFlight(card("unknown")), true);
  assert.equal(orderCardInFlight(card("awaiting_payment")), true);
  assert.equal(orderCardInFlight(card(null)), false);
  assert.equal(orderCardInFlight(card("placed")), false);
  assert.equal(orderCardInFlight(card("unknown", true)), false);
  assert.equal(orderCardInFlight(null), false);
});
