import assert from "node:assert/strict";
import test from "node:test";
import {
  UNDO_WINDOW_MS,
  badgeFor,
  canUndo,
  correctionPayload,
  editableFields,
  groupFor,
  groupMemories,
  memorySentence,
  originOf,
  preferencePermissionState,
} from "../../app/memory-groups.mjs";

const NOW = Date.parse("2026-11-22T12:00:00Z");
const hoursAgo = (hours) => new Date(NOW - hours * 3600_000).toISOString();

function memory(overrides = {}) {
  return {
    id: "m1",
    memory_type: "brand_preference",
    value: { brand: "Amul Taaza", product_category: "milk" },
    text: "Prefers Amul Taaza milk.",
    source_type: "explicit_statement",
    source_conversation_id: null,
    status: "confirmed",
    sensitivity: "low",
    confidence: 0.9,
    allowed_purposes: ["personalize_search"],
    expires_at: null,
    confirmed_at: hoursAgo(48),
    deleted_at: null,
    created_at: hoursAgo(48),
    ...overrides,
  };
}

test("groups preferences, avoidances and rules", () => {
  assert.equal(groupFor({ memory_type: "brand_preference" }), "pasand");
  assert.equal(groupFor({ memory_type: "pack_size_preference" }), "pasand");
  assert.equal(groupFor({ memory_type: "store_preference" }), "pasand");
  assert.equal(groupFor({ memory_type: "product_avoidance" }), "kabhi_nahi");
  assert.equal(groupFor({ memory_type: "store_avoidance" }), "kabhi_nahi");
  assert.equal(groupFor({ memory_type: "brand_avoidance" }), "kabhi_nahi");
  assert.equal(groupFor({ memory_type: "dietary_preference" }), "niyam");
  assert.equal(groupFor({ memory_type: "household_rule" }), "niyam");
  assert.equal(groupFor({ memory_type: "something_new" }), "niyam");
});

test("writes readable Hinglish sentences", () => {
  assert.equal(memorySentence(memory()), "Doodh: hamesha Amul Taaza");
  assert.equal(memorySentence(memory({ value: { brand: "Fortune", product_category: "cooking oil", pack_size: "1 litre" } })), "Tel: hamesha Fortune, 1 litre");
  assert.equal(memorySentence(memory({ value: { brand: "Aavin", alternative_brand: "Nandini", product_category: "milk" } })), "Doodh: hamesha Aavin ya Nandini");
  assert.equal(memorySentence(memory({ memory_type: "pack_size_preference", value: { pack_size: "5 kg", product_category: "atta" } })), "Atta: hamesha 5 kg");
  assert.equal(memorySentence(memory({ memory_type: "quantity_preference", value: { quantity: 2, unit: "packet", product_category: "bread" } })), "Bread: aam taur par 2 packet");
  assert.equal(memorySentence(memory({ memory_type: "product_avoidance", value: { product: "Maggi" } })), "Maggi: kabhi nahi lena");
  assert.equal(memorySentence(memory({ memory_type: "product_avoidance", value: { brand: "Mother Dairy", product_category: "milk" } })), "Doodh: Mother Dairy kabhi nahi");
  assert.equal(memorySentence(memory({ memory_type: "store_preference", value: { store: "zepto", product_category: "milk" } })), "Doodh: hamesha Zepto se");
  assert.equal(memorySentence(memory({ memory_type: "store_avoidance", value: { store: "swiggy" } })), "Swiggy Instamart: kabhi nahi");
  assert.equal(memorySentence(memory({ memory_type: "dietary_preference", value: { preference: "sirf veg" } })), "Khaana: sirf veg");
  assert.equal(memorySentence(memory({ memory_type: "household_rule", value: {}, text: "deliveries after 7pm only" })), "Deliveries after 7pm only");
});

test("origin badges, defaulting a missing origin to 'aapne bataya'", () => {
  assert.equal(originOf(memory()), "explicit");
  assert.equal(badgeFor(memory(), NOW), "aapne bataya");
  assert.equal(badgeFor(memory({ origin: "explicit" }), NOW), "aapne bataya");
  assert.equal(badgeFor(memory({ origin: "inferred" }), NOW), "suggestion");
  assert.equal(badgeFor(memory({ origin: "stated", learned_at: hoursAgo(2) }), NOW), "abhi seekha");
  assert.equal(badgeFor(memory({ origin: "stated", learned_at: hoursAgo(30) }), NOW), "aapne bataya");
  assert.equal(badgeFor(memory({ origin: "bogus" }), NOW), "aapne bataya");
});

test("undo only for confirmed stated memories inside 24 hours", () => {
  assert.equal(canUndo(memory({ origin: "stated", learned_at: hoursAgo(1) }), NOW), true);
  assert.equal(canUndo(memory({ origin: "stated", learned_at: new Date(NOW - UNDO_WINDOW_MS + 1000).toISOString() }), NOW), true);
  assert.equal(canUndo(memory({ origin: "stated", learned_at: new Date(NOW - UNDO_WINDOW_MS).toISOString() }), NOW), false);
  assert.equal(canUndo(memory({ origin: "explicit", learned_at: hoursAgo(1) }), NOW), false);
  assert.equal(canUndo(memory({ learned_at: hoursAgo(1) }), NOW), false, "missing origin never offers undo");
  assert.equal(canUndo(memory({ origin: "stated", learned_at: hoursAgo(1), status: "proposed" }), NOW), false);
  assert.equal(canUndo(memory({ origin: "stated", learned_at: null, confirmed_at: hoursAgo(3) }), NOW), true, "falls back to confirmed_at");
  assert.equal(canUndo(memory({ origin: "stated", learned_at: new Date(NOW + 60_000).toISOString() }), NOW), false, "future timestamps are not trusted");
});

test("groupMemories splits suggestions, drops inactive records, sorts newest first", () => {
  const view = groupMemories([
    memory({ id: "old", confirmed_at: hoursAgo(72), created_at: hoursAgo(72) }),
    memory({ id: "new", value: { brand: "Nandini", product_category: "milk" }, confirmed_at: hoursAgo(1), created_at: hoursAgo(1) }),
    memory({ id: "avoid", memory_type: "product_avoidance", value: { product: "Maggi" } }),
    memory({ id: "pending", status: "proposed", confirmed_at: null }),
    memory({ id: "rejected", status: "rejected" }),
    memory({ id: "superseded", status: "superseded" }),
    memory({ id: "deleted", deleted_at: hoursAgo(1) }),
    memory({ id: "expired", expires_at: hoursAgo(1) }),
  ], NOW);
  assert.deepEqual(view.groups.map((group) => group.id), ["pasand", "kabhi_nahi"]);
  assert.deepEqual(view.groups[0].rows.map((row) => row.id), ["new", "old"]);
  assert.deepEqual(view.suggestions.map((row) => row.id), ["pending"]);
  assert.equal(view.total, 3);
  assert.equal(view.groups[0].rows[0].date, "22 Nov");
});

test("groupMemories tolerates empty or bad input", () => {
  assert.deepEqual(groupMemories([], NOW), { groups: [], suggestions: [], total: 0 });
  assert.deepEqual(groupMemories(null, NOW), { groups: [], suggestions: [], total: 0 });
});

test("editable fields and correction payloads", () => {
  const original = memory();
  assert.deepEqual(editableFields(original).map((field) => field.key), ["brand", "product_category"]);
  assert.equal(correctionPayload(original, { brand: "Amul Taaza" }), null, "no change → no request");
  assert.equal(correctionPayload(original, { brand: "  " }), null, "required field cannot be blanked");

  const payload = correctionPayload(original, { brand: "Amul Gold" });
  assert.deepEqual(payload.value, { brand: "Amul Gold", product_category: "milk" });
  assert.equal(payload.text, "Doodh: hamesha Amul Gold");
  assert.equal(payload.source_type, "correction");
  assert.equal(payload.memory_type, "brand_preference");
  assert.deepEqual(payload.allowed_purposes, ["personalize_search"]);
  assert.ok(payload.reason.length >= 2);

  const dropped = correctionPayload(original, { product_category: "" });
  assert.deepEqual(dropped.value, { brand: "Amul Taaza" });

  const freeText = memory({ memory_type: "household_rule", value: {}, text: "Deliveries after 7pm" });
  assert.deepEqual(editableFields(freeText), [{ key: "text", label: "Yaad", value: "Deliveries after 7pm" }]);
  assert.equal(correctionPayload(freeText, { text: "Deliveries after 8pm" }).text, "Deliveries after 8pm");
  assert.equal(correctionPayload(freeText, { text: "x" }), null);
});

test("preference permission state", () => {
  const categories = ["brand_preference", "product_avoidance"];
  const row = (data_category, allowed, extra = {}) => ({ data_category, purpose: "personalize_search", allowed, ...extra });
  assert.equal(preferencePermissionState([], categories), "off");
  assert.equal(preferencePermissionState([row("brand_preference", true)], categories), "partial");
  assert.equal(preferencePermissionState([row("brand_preference", true), row("product_avoidance", true)], categories), "on");
  assert.equal(preferencePermissionState([row("brand_preference", true), row("product_avoidance", true, { revoked_at: hoursAgo(1) })], categories), "partial");
  assert.equal(preferencePermissionState([{ data_category: "brand_preference", purpose: "proactive_reorder", allowed: true }], categories), "off");
});
