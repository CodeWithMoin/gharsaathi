import assert from "node:assert/strict";
import test from "node:test";
import { PHOTO_DIFFERENT_NOTE, PHOTO_MATCH_BADGE, orderOptions, photoMatch } from "../../app/product-options.mjs";
import { PHOTO_BETA_LABEL, PHOTO_BETA_NOTE } from "../../app/photo-attachment.mjs";

const option = (name, extra = {}) => ({ product_id: name, sku_id: `${name}-sku`, name, available: true, price: { mrp: 30, offer_price: 25 }, ...extra });

test("an option that looks like the user's photo carries the plain-English badge", () => {
  assert.equal(PHOTO_MATCH_BADGE, "Looks like your photo");
  assert.deepEqual(photoMatch({ visual_match: "same_product" }), { badge: "Looks like your photo", note: null });
  assert.deepEqual(photoMatch({ visual_match: "different" }), { badge: null, note: PHOTO_DIFFERENT_NOTE });
  assert.deepEqual(photoMatch({ visual_match: "similar" }), { badge: null, note: null });
  assert.deepEqual(photoMatch({}), { badge: null, note: null });
  assert.deepEqual(photoMatch({ visual_match: "<b>x</b>" }), { badge: null, note: null });
});

test("the backend's photo order is kept and card 1 says it looks like the photo", () => {
  const ordered = orderOptions([
    option("Amul Lassi", { position: 1, visual_match: "same_product", remembered: "purchase_history" }),
    option("Smoodh", { position: 2, visual_match: "similar" }),
    option("Amul Kool", { position: 3, visual_match: "different" }),
  ]);
  assert.deepEqual(ordered.map((item) => [item.product.name, item.number, item.badge]), [
    ["Amul Lassi", 1, "Looks like your photo"],
    ["Smoodh", 2, null],
    ["Amul Kool", 3, null],
  ]);
});

test("a sold-out same pack further down still shows the photo badge; other cards keep the usual badge", () => {
  const ordered = orderOptions([
    option("Sweet lassi", { position: 1 }),
    option("Amul Lassi", { position: 2, available: false, visual_match: "same_product" }),
  ]);
  assert.deepEqual(ordered.map((item) => item.badge), ["Best match", "Looks like your photo"]);
});

test("shop from a photo is labelled beta", () => {
  assert.equal(PHOTO_BETA_LABEL, "Beta");
  assert.match(PHOTO_BETA_NOTE, /beta/i);
});
