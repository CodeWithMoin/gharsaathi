import assert from "node:assert/strict";
import test from "node:test";
import {
  activeIndexFromScroll,
  basketActionLabel,
  basketQuantity,
  bestPickBadge,
  MAX_OPTIONS,
  optionPositionLabel,
  optionPrices,
  optionTitle,
  orderOptions,
  positionLabel,
  storeName,
} from "../../app/product-options.mjs";

const option = (name, extra = {}) => ({ product_id: name, sku_id: `${name}-sku`, spin_id: `${name}-spin`, name, available: true, price: { mrp: 100, offer_price: 90 }, ...extra });
const names = (options) => options.map((item) => item.product.name);

test("without annotations the backend's order is kept and the first card is the best match", () => {
  const ordered = orderOptions([option("A"), option("B"), option("C")]);
  assert.deepEqual(names(ordered), ["A", "B", "C"]);
  assert.deepEqual(ordered.map((item) => [item.number, item.badge]), [[1, "Best match"], [2, null], [3, null]]);
});

test("the remembered option comes first, then a preferred one, then the rest in backend order", () => {
  const ordered = orderOptions([option("A"), option("B", { preferred: true }), option("C"), option("D", { remembered: "purchase_history" })]);
  assert.deepEqual(names(ordered), ["D", "B", "A", "C"]);
  assert.equal(ordered[0].badge, "You bought this before");
});

test("a preferred first pick says Your usual, and so does a confirmed remembered preference", () => {
  assert.equal(orderOptions([option("A"), option("B", { preferred: true })])[0].badge, "Your usual");
  assert.equal(bestPickBadge(option("X", { remembered: "confirmed_preference" })), "Your usual");
});

test("options the household asked to avoid are hidden", () => {
  const ordered = orderOptions([option("Avoid", { avoided: true }), option("A"), option("B")]);
  assert.deepEqual(names(ordered), ["A", "B"]);
  assert.deepEqual(ordered.map((item) => item.number), [1, 2]);
});

test("unavailable options go last; a remembered but unavailable option is not the best pick", () => {
  const ordered = orderOptions([option("Gone", { available: false, remembered: "purchase_history" }), option("A"), option("B")]);
  assert.deepEqual(names(ordered), ["A", "B", "Gone"]);
  assert.equal(ordered[0].badge, "Best match");
  assert.equal(orderOptions([option("Gone", { available: false })])[0].badge, null);
});

test("the same store item is shown once; the same item at two stores is two options", () => {
  const ordered = orderOptions([option("A"), option("A"), option("A", { provider: "zepto" })]);
  assert.equal(ordered.length, 2);
  assert.deepEqual(ordered.map((item) => item.product.provider ?? "swiggy_instamart"), ["swiggy_instamart", "zepto"]);
});

test("options are capped and unnamed entries are skipped", () => {
  const many = Array.from({ length: MAX_OPTIONS + 4 }, (_, index) => option(`P${index}`));
  assert.equal(orderOptions(many).length, MAX_OPTIONS);
  assert.equal(orderOptions([option("")]).length, 0);
});

test("backend positions decide the order and the card numbers; nothing is re-sorted or hidden", () => {
  // The backend already put the remembered option first; the model calls it position 1.
  const sent = [
    option("Remembered", { remembered: "purchase_history", position: 1 }),
    option("Provider first", { position: 2 }),
    option("Preferred later", { preferred: true, position: 3 }),
    option("Gone", { available: false, position: 4 }),
  ];
  const shown = orderOptions(sent);
  assert.deepEqual(names(shown), ["Remembered", "Provider first", "Preferred later", "Gone"]);
  assert.deepEqual(shown.map((item) => item.number), [1, 2, 3, 4]);
  assert.deepEqual(shown.map((item) => item.badge), ["You bought this before", null, null, null]);
});

test("card N is the product the backend sent as position N, even out of array order", () => {
  const shown = orderOptions([option("Second", { position: 2 }), option("Third", { preferred: true, position: 3 }), option("First", { position: 1 })]);
  assert.deepEqual(shown.map((item) => [item.number, item.product.name]), [[1, "First"], [2, "Second"], [3, "Third"]]);
  assert.equal(shown[0].badge, "Best match");
});

test("positioned results show an avoided flag as-is instead of renumbering around it", () => {
  const shown = orderOptions([option("A", { position: 1 }), option("B", { avoided: true, position: 2 })]);
  assert.deepEqual(shown.map((item) => [item.number, item.product.name]), [[1, "A"], [2, "B"]]);
});

test("results without positions (old chats, typed search) keep the fallback order", () => {
  const ordered = orderOptions([option("A"), option("B", { preferred: true }), option("Gone", { available: false }), option("Avoid", { avoided: true })]);
  assert.deepEqual(names(ordered), ["B", "A", "Gone"]);
  assert.deepEqual(ordered.map((item) => item.number), [1, 2, 3]);
  // A partial set of positions is not trusted; the whole group falls back.
  assert.deepEqual(names(orderOptions([option("A", { position: 2 }), option("B", { remembered: "purchase_history" })])), ["B", "A"]);
});

test("position and accessible labels", () => {
  assert.equal(positionLabel(0, 3), "1/3");
  assert.equal(positionLabel(5, 3), "3/3");
  assert.equal(optionPositionLabel(2, 3), "Option 2 of 3");
  assert.equal(storeName({ provider: "zepto" }), "Zepto");
  assert.equal(storeName({}, "demo"), "Swiggy demo");
  assert.equal(optionTitle({ name: "Amul Taaza", pack_size: "1 L" }), "Amul Taaza 1 L");
  assert.equal(optionTitle({ name: "Amul Taaza 1L", pack_size: "1 L" }), "Amul Taaza 1L");
  assert.equal(basketActionLabel({ name: "Amul Taaza", pack_size: "1 L", provider: "zepto" }, "add"), "Add Amul Taaza 1 L to Zepto basket");
  assert.equal(basketActionLabel({ name: "Amul Taaza", provider: "zepto" }, "remove"), "Remove Amul Taaza from Zepto basket");
  assert.equal(basketActionLabel({ name: "Atta" }, "increase"), "Increase quantity of Atta in Swiggy basket");
});

test("prices are the backend's strings, never built in the browser", () => {
  assert.deepEqual(optionPrices(option("A", { price: { mrp: 180, offer_price: 154, offer_display: "₹154", mrp_display: "₹180", saving_display: "₹26" } })), { price: "₹154", mrp: "₹180", saving: "₹26" });
  // A product without display strings shows no price rather than a computed one.
  assert.deepEqual(optionPrices(option("A")), { price: null, mrp: null, saving: null });
});

test("basket quantity matches the store and variant", () => {
  const baskets = { zepto: { lines: [{ ref: { provider: "zepto", variant_id: "A-spin" }, quantity: 2 }] } };
  assert.equal(basketQuantity(baskets, option("A", { provider: "zepto" })), 2);
  assert.equal(basketQuantity(baskets, option("A")), 0);
  assert.equal(basketQuantity(baskets, option("B", { provider: "zepto" })), 0);
  assert.equal(basketQuantity(baskets, { provider: "zepto" }), 0);
});

test("the current card follows the scroll position, and the end of the range is the last card", () => {
  const offsets = [8, 248, 488, 728];
  assert.equal(activeIndexFromScroll(offsets, 0, 600), 0);
  assert.equal(activeIndexFromScroll(offsets, 130, 600), 1);
  assert.equal(activeIndexFromScroll(offsets, 480, 600), 2);
  assert.equal(activeIndexFromScroll(offsets, 599, 600), 3);
  assert.equal(activeIndexFromScroll([], 0, 0), 0);
  assert.equal(activeIndexFromScroll([0, 200], 0, 0), 0);
});
