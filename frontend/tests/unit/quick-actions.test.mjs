import assert from "node:assert/strict";
import test from "node:test";
import {
  CLEAR_CONFLICT_TEXT,
  GET_BILL_HINT,
  MAX_CHIPS,
  WHERE_IS_MY_ORDER,
  addReportText,
  basketEditState,
  chipUserText,
  clearBasketCopy,
  draftDirty,
  draftPayload,
  draftRemove,
  draftRename,
  draftStep,
  headerOrderChip,
  listDraft,
  listNameProblem,
  quickChips,
  stepQuantity,
  swapQuery,
  undoQuantity,
} from "../../app/quick-actions.mjs";

const ref = (variant, provider = "zepto") => ({ provider, product_id: `p_${variant}`, variant_id: variant });
const item = (variant, name, quantity = 1) => ({ ref: ref(variant), name, pack_size: "500 ml", quantity });
const actions = (overrides = {}) => ({
  last_order: { provider: "zepto", store: "Zepto", item_count: 3, item_names: ["Milk"] },
  recent_items: [item("v1", "Amul Milk"), item("v2", "Amul Curd"), item("v3", "Amul Ghee"), item("v4", "Bread")],
  lists: [],
  ...overrides,
});
const kinds = (chips) => chips.map((chip) => chip.action.kind);

test("chips: reorder, where (only while an order moves), three recent items, My lists", () => {
  assert.deepEqual(kinds(quickChips({ actions: actions(), orderActive: false })), ["repeat", "recent", "recent", "recent", "lists"]);
  const moving = quickChips({ actions: actions(), orderActive: true });
  assert.deepEqual(kinds(moving), ["repeat", "where", "recent", "recent", "recent", "lists"]);
  assert.equal(moving.length, MAX_CHIPS);
  assert.equal(moving[1].label, WHERE_IS_MY_ORDER);
});

test("chips: no last order and no history leaves only My lists", () => {
  assert.deepEqual(kinds(quickChips({ actions: actions({ last_order: null, recent_items: [] }), orderActive: false })), ["lists"]);
  assert.deepEqual(kinds(quickChips({ actions: null, orderActive: false })), ["lists"]);
});

test("chips: a short row drops recent items first and keeps My lists", () => {
  assert.deepEqual(kinds(quickChips({ actions: actions(), orderActive: true, max: 4 })), ["repeat", "where", "recent", "lists"]);
});

test("chips: duplicate recent products show once", () => {
  const chips = quickChips({ actions: actions({ recent_items: [item("v1", "Milk"), item("v1", "Milk")] }), orderActive: false });
  assert.equal(chips.filter((chip) => chip.action.kind === "recent").length, 1);
});

test("chip taps read as the user's own action", () => {
  const [repeat, recent] = quickChips({ actions: actions(), orderActive: false });
  assert.equal(chipUserText(repeat), "Reorder my last order");
  assert.equal(chipUserText(recent), "Add Amul Milk 500 ml");
});

test("add report: added count, reasons in plain English, never 'not found' for an error", () => {
  const summary = addReportText({
    added_count: 1,
    items: [
      { name: "Milk", store: "Zepto", provider: "zepto", quantity: 2, status: "added" },
      { name: "Curd", store: "Zepto", provider: "zepto", quantity: 1, status: "unavailable" },
      { name: "Atta", store: "Swiggy Instamart", provider: "swiggy_instamart", quantity: 1, status: "store_error" },
    ],
    baskets: [{ provider: "zepto", lines: [] }],
  }, { source: "your last order" });
  assert.equal(summary.text, `Added 2 items from your last order to your basket. Not added: Curd (unavailable at Zepto right now), Atta (Swiggy Instamart couldn't be reached). ${GET_BILL_HINT}`);
  assert.equal(summary.offerBill, true);
  assert.deepEqual(summary.providers, ["zepto"]);
  assert.doesNotMatch(summary.text, /not found/i);
});

test("add report: nothing added offers no bill", () => {
  const summary = addReportText({ added_count: 0, items: [{ name: "Milk", store: "Zepto", provider: "zepto", quantity: 1, status: "not_found" }], baskets: [] }, { source: "“Weekly list”" });
  assert.equal(summary.text, "Nothing from “Weekly list” was added. Not added: Milk (not listed at Zepto today).");
  assert.equal(summary.offerBill, false);
});

test("undo puts the line back to its quantity before the add", () => {
  assert.equal(undoQuantity({ lines: [{ ref: ref("v1"), quantity: 3 }] }, ref("v1"), 1), 2);
  assert.equal(undoQuantity({ lines: [{ ref: ref("v1"), quantity: 1 }] }, ref("v1"), 1), 0);
  assert.equal(undoQuantity({ lines: [] }, ref("v1"), 1), null);
});

test("basket card edits: allowed on a draft, refused with a reason in checkout", () => {
  assert.deepEqual(basketEditState({ status: "draft", lines: [{}] }), { canEdit: true, canClear: true, reason: null });
  assert.deepEqual(basketEditState({ status: "draft", lines: [] }), { canEdit: true, canClear: false, reason: null });
  assert.equal(basketEditState({ status: "draft", lines: [{}] }, { busy: true }).canClear, false);
  const pending = basketEditState({ status: "checkout_pending", lines: [{}] });
  assert.equal(pending.canClear, false);
  assert.match(pending.reason, /confirming an order/);
  assert.equal(basketEditState({ status: "ordered", lines: [{}] }).reason, "This basket was ordered.");
});

test("clear copy names the store and count; − / + stay in range; swap searches the item", () => {
  assert.equal(clearBasketCopy({ provider: "zepto", lines: [{ quantity: 2 }, { quantity: 1 }] }).body, "This removes all 3 items from your Zepto basket. Any bill or order card for it stops working.");
  assert.equal(stepQuantity(1, -1), 0);
  assert.equal(stepQuantity(0, -1), null);
  assert.equal(stepQuantity(50, 1), null);
  assert.equal(swapQuery({ name: "Amul  Taaza Milk", pack_size: "500 ml" }), "Amul Taaza Milk 500 ml");
  assert.equal(swapQuery({ name: "Amul Milk 500 ml", pack_size: "500 ml" }), "Amul Milk 500 ml");
});

test("list editing: steps, removal, rename, dirty and payload", () => {
  const list = { id: "l1", name: "Weekly list", items: [item("v1", "Milk", 2), item("v2", "Curd")], item_count: 2 };
  let draft = listDraft(list);
  assert.equal(draftDirty(draft, list), false);
  draft = draftStep(draft, 1, -1);
  assert.equal(draft.items[1].quantity, 1, "never below 1");
  draft = draftStep(draft, 0, 1);
  assert.equal(draft.items[0].quantity, 3);
  assert.equal(list.items[0].quantity, 2, "the saved list is untouched");
  assert.equal(draftDirty(draft, list), true);
  draft = draftRemove(draftRename(draft, "  Weekly   ration "), 1);
  assert.deepEqual(draftPayload(draft), { name: "Weekly ration", items: [{ ref: ref("v1"), quantity: 3 }] });
  assert.equal(draftPayload(draftRename(draft, "   ")), null);
});

test("list names: required, short, unique (any case)", () => {
  assert.equal(listNameProblem(" "), "Give the list a name.");
  assert.equal(listNameProblem("x".repeat(61)), "Keep the name under 60 characters.");
  assert.equal(listNameProblem("weekly LIST", [{ name: "Weekly list" }]), "You already have a list called “weekly LIST”.");
  assert.equal(listNameProblem("Monthly ration", [{ name: "Weekly list" }]), null);
});

const card = (overrides) => ({ provider: "zepto", orderId: "Z1", store: "Zepto", seq: 1, status: "confirmed", etaAt: null, active: true, ...overrides });
const at = (hours, minutes) => new Date(2026, 10, 22, hours, minutes).toISOString();
const now = new Date(2026, 10, 22, 8, 20).getTime();

test("header chip: arriving, out for delivery, running late, from the store's own ETA", () => {
  assert.equal(headerOrderChip([card({ etaAt: at(8, 26) })], now).text, "Zepto · arriving 8:26");
  assert.equal(headerOrderChip([card({ status: "out_for_delivery", etaAt: at(8, 26) })], now).text, "Out for delivery · 8:26");
  assert.equal(headerOrderChip([card({ status: "out_for_delivery" })], now).text, "Out for delivery");
  const late = headerOrderChip([card({ status: "out_for_delivery", etaAt: at(8, 10) })], now);
  assert.equal(late.text, "Running late");
  assert.equal(late.label, "Zepto order: Running late. Show the order status");
  assert.equal(late.tone, "late");
  assert.equal(headerOrderChip([card({ status: "preparing" })], now).text, "Zepto · Being packed");
});

test("header chip: only a moving order, the newest one", () => {
  assert.equal(headerOrderChip([card({ status: "delivered" })], now), null);
  assert.equal(headerOrderChip([card({ status: "cancelled" })], now), null);
  assert.equal(headerOrderChip([card({ active: false })], now), null);
  assert.equal(headerOrderChip([], now), null);
  assert.equal(headerOrderChip([card({ orderId: "old", seq: 1 }), card({ orderId: "new", seq: 4 })], now).orderId, "new");
});

test("a clear that lost a race asks the user to review", () => {
  assert.equal(CLEAR_CONFLICT_TEXT, "This basket changed. Review it and try again.");
});
