import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { firstEmptyField, mergeAddressPrefill, settleAddressFormAck } from "../../app/address-form-merge.mjs";

test("prefill fills empty untouched fields and never overwrites what the user typed", () => {
  const touched = new Set(["flat_details"]);
  const start = { type: "HOME", flat_details: "A-12", building_name: "" };
  const { values, filled, kept } = mergeAddressPrefill(start, { flat_details: "Flat 9", building_name: "RMZ The Loft", floor: "3" }, touched);
  assert.equal(values.flat_details, "A-12");
  assert.equal(values.building_name, "RMZ The Loft");
  assert.equal(values.floor, "3");
  assert.deepEqual(filled, ["building_name", "floor"]);
  assert.deepEqual(kept, ["flat_details"]);
});

test("a follow-up prefill keeps earlier assistant values and adds new ones", () => {
  const first = mergeAddressPrefill({ type: "HOME" }, { building_name: "RMZ The Loft" }, new Set());
  const second = mergeAddressPrefill(first.values, { building_name: "RMZ The Loft", floor: "3", short_address: "Gachibowli" }, new Set());
  assert.deepEqual(second.filled, ["floor", "short_address"]);
  assert.equal(second.values.building_name, "RMZ The Loft");
});

test("contact number and unknown keys are never prefilled", () => {
  const { values, filled } = mergeAddressPrefill({}, { contact_number: "9876543210", latitude: "12.9", building_name: " " }, new Set());
  assert.deepEqual(values, {});
  assert.deepEqual(filled, []);
});

test("first empty field follows the form's required order", () => {
  assert.equal(firstEmptyField({ building_name: "RMZ The Loft" }), "flat_details");
  assert.equal(firstEmptyField({ flat_details: "1", building_name: "b", formatted_address: "f", short_address: "s", name: "n", contact_name: "c" }), "contact_number");
});

test("the screen acknowledgement always settles and reports form_visible", async () => {
  assert.deepEqual(await settleAddressFormAck(undefined), { form_visible: false, reason: "not_handled", filled_fields: [], kept_user_fields: [] });
  assert.equal((await settleAddressFormAck(Promise.reject(new Error("boom")))).reason, "render_failed");
  assert.equal((await settleAddressFormAck(new Promise(() => {}), { timeoutMs: 5 })).reason, "render_timeout");
  const shown = await settleAddressFormAck(Promise.resolve({ form_visible: true, filled_fields: ["floor", "contact_number"], kept_user_fields: ["name"] }));
  assert.deepEqual(shown, { form_visible: true, reason: "shown", filled_fields: ["floor"], kept_user_fields: ["name"] });
});

test("form placeholders are hints, never sample addresses or coordinates", () => {
  const source = readFileSync(new URL("../../app/zepto-address-form.tsx", import.meta.url), "utf8");
  for (const sample of ["Flat 4B", "Lake View Apartments", "Indiranagar", "Near metro station", "12.971599", "77.594566"]) {
    assert.equal(source.includes(sample), false, sample);
  }
  assert.equal(/defaultValue=\{initial/.test(source), false);
});
