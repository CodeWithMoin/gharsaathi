import assert from "node:assert/strict";
import test from "node:test";
import { SOURCE_LABELS, correctionPayload, editableFields, profileResetPayload, summaryView } from "../../app/memory-groups.mjs";

const NOW = Date.parse("2026-11-22T12:00:00Z");
const record = (id, value, extra = {}) => ({
  id, memory_type: "x", value, text: "t", status: "confirmed", origin: "explicit",
  confirmed_at: "2026-11-20T10:00:00Z", allowed_purposes: ["personalize_search"], ...extra,
});
const item = (id, group, label, extra = {}) => ({ id, group, label, source: "told", updated_at: "2026-11-20T10:00:00Z", managed_by: "memory", ...extra });

const RECORDS = [
  record("m-diet", { kind: "diet", diet: "jain" }, { memory_type: "diet_preference" }),
  record("m-milk", { kind: "prefer_brand", product_category: "milk", brand: "Amul", pack_size: "1 L", quantity: "2" }, { memory_type: "brand_preference" }),
  record("m-people", { kind: "household", product_category: "people", fact: "2" }, { memory_type: "household_fact" }),
  record("m-said", { kind: "avoid_brand", brand: "Brand Y", origin: "stated" }, { memory_type: "brand_avoidance", origin: "stated", confirmed_at: "2026-11-22T11:00:00Z" }),
];
const SUMMARY = {
  consent: { saved_preferences: "on", purchase_history: "on" },
  groups: [
    { id: "home", title: "Home", items: [item("profile:language", "home", "Reply language: English", { managed_by: "profile", kind: "language" }), item("m-people", "home", "People at home: 2")] },
    { id: "food", title: "Food", items: [item("m-diet", "food", "Diet: Jain food")] },
    { id: "avoid", title: "Avoid", items: [item("m-said", "avoid", "Avoid Brand Y", { source: "in_passing" })] },
    { id: "usual_buys", title: "Usual buys", items: [item("m-milk", "usual_buys", "Milk: always Amul, 1 L, usually 2"), item("m-gone", "usual_buys", "Deleted elsewhere")] },
    { id: "from_orders", title: "From your orders", items: [item("order:1", "from_orders", "Diet Coke — bought 9 times on Zepto", { managed_by: "order", source: "order_history" })] },
    { id: "nonsense", title: "x", items: [item("m-diet", "x", "x")] },
  ],
  suggestions: [],
  total: 6,
};

test("summaryView keeps screen group order, drops unknown groups and rows without a record", () => {
  const view = summaryView(SUMMARY, RECORDS, NOW);
  assert.deepEqual(view.groups.map((group) => group.id), ["home", "food", "usual_buys", "avoid", "from_orders"]);
  assert.deepEqual(view.groups.find((group) => group.id === "usual_buys").rows.map((row) => row.id), ["m-milk"]);
  assert.equal(view.total, 6);
  assert.equal(view.deletable, 4); // memory rows only: profile resets and orders are separate
  assert.equal(view.consent, "on");
});

test("rows carry source badges, dates and what can be edited or deleted", () => {
  const rows = Object.fromEntries(summaryView(SUMMARY, RECORDS, NOW).groups.flatMap((group) => group.rows).map((row) => [row.id, row]));
  assert.equal(rows["m-diet"].badge, SOURCE_LABELS.told);
  assert.equal(rows["m-said"].badge, "Just learned");
  assert.equal(rows["m-said"].undoable, true);
  assert.equal(rows["order:1"].badge, "From your orders");
  assert.equal(rows["order:1"].deletable, false);
  assert.equal(rows["order:1"].editable, false);
  assert.equal(rows["profile:language"].editable, true);
  assert.equal(rows["profile:language"].record, null);
  assert.equal(rows["m-diet"].date, "20 Nov");
});

test("fixed-set answers edit with choices; a household question is not retyped", () => {
  const diet = editableFields(RECORDS[0]);
  assert.deepEqual(diet.map((field) => field.key), ["diet"]);
  assert.ok(diet[0].options.some((option) => option.value === "jain"));
  const people = editableFields(RECORDS[2]);
  assert.deepEqual(people.map((field) => [field.key, field.label]), [["fact", "People at home"]]);
  assert.equal(people[0].options.length, 15);
  const milk = editableFields(RECORDS[1]).map((field) => field.key);
  assert.deepEqual(milk, ["product_category", "brand", "pack_size", "quantity"]);
  const payload = correctionPayload(RECORDS[1], { quantity: "3" });
  assert.equal(payload.value.quantity, "3");
  assert.equal(payload.text, "Milk: always Amul, 1 L, usually 3");
});

test("profile rows reset to the defaults", () => {
  assert.deepEqual(profileResetPayload("language"), { language: "hinglish" });
  assert.deepEqual(profileResetPayload("address_form"), { address_form: "neutral" });
  assert.deepEqual(profileResetPayload("first_name"), { first_name: null });
  assert.equal(profileResetPayload("diet"), null);
});

test("an empty or missing summary is an empty view", () => {
  assert.deepEqual(summaryView(null, [], NOW).groups, []);
  assert.equal(summaryView(undefined, [], NOW).total, 0);
});
