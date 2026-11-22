import assert from "node:assert/strict";
import test from "node:test";
import { addressLabel, addressSummary, disconnectCopy, initialEditValues, profileChanges, profileDraft, selectedAddress } from "../../app/input-flows.mjs";
import { hasUnsavedChanges } from "../../app/ui/input-dialog-logic.mjs";

const addresses = [
  { id: "a1", tag: "Home", address_line: "12 MG Road" },
  { id: "a2", category: "Work", address_line: "Tower B" },
  { id: "a3", address_line: "Somewhere" },
];

test("selected address is found only by exact id", () => {
  assert.equal(selectedAddress(addresses, "a2")?.id, "a2");
  assert.equal(selectedAddress(addresses, "missing"), null);
  assert.equal(selectedAddress(addresses, ""), null);
  assert.equal(selectedAddress(undefined, "a1"), null);
});

test("address labels fall back from tag to category to a plain name", () => {
  assert.deepEqual(addresses.map(addressLabel), ["Home", "Work", "Saved address"]);
});

test("setup sheet summary says what is chosen, or how many are saved", () => {
  assert.deepEqual(addressSummary(addresses, "a1"), { title: "Home", detail: "12 MG Road", chosen: true });
  assert.deepEqual(addressSummary(addresses, "gone"), { title: "No address chosen", detail: "3 saved addresses", chosen: false });
  assert.deepEqual(addressSummary([addresses[0]], ""), { title: "No address chosen", detail: "1 saved address", chosen: false });
  assert.equal(addressSummary([], "").title, "No saved address yet");
});

test("editing a preference is dirty only after a real change", () => {
  const initial = initialEditValues([{ key: "brand", value: "Amul" }, { key: "text" }]);
  assert.deepEqual(initial, { brand: "Amul", text: "" });
  assert.equal(hasUnsavedChanges(initial, { ...initial, brand: " Amul " }), false);
  assert.equal(hasUnsavedChanges(initial, { ...initial, brand: "Mother Dairy" }), true);
});

test("profile draft prefers the account language and safe defaults", () => {
  assert.deepEqual(profileDraft({ first_name: "Moin", address_form: "masculine", language: "hindi" }, "English"), { first_name: "Moin", address_form: "masculine", language: "english" });
  assert.deepEqual(profileDraft({ first_name: null, address_form: "odd", language: "hindi" }, "Klingon"), { first_name: "", address_form: "neutral", language: "hindi" });
  assert.deepEqual(profileDraft(null), { first_name: "", address_form: "neutral", language: "hinglish" });
});

test("profile save sends only what changed", () => {
  const initial = { first_name: "Moin", address_form: "neutral", language: "hinglish" };
  assert.equal(profileChanges(initial, { ...initial, first_name: " Moin " }), null);
  assert.deepEqual(profileChanges(initial, { ...initial, first_name: "" }), { first_name: null });
  assert.deepEqual(profileChanges(initial, { first_name: "Mo", address_form: "feminine", language: "english" }), { first_name: "Mo", address_form: "feminine", language: "english" });
  assert.deepEqual(profileChanges(initial, { ...initial, language: "hindi" }), { language: "hindi" });
});

test("disconnect confirmation names the store and what is kept", () => {
  const swiggy = disconnectCopy("swiggy");
  assert.equal(swiggy.title, "Disconnect Swiggy Instamart?");
  assert.match(swiggy.body, /account and orders are not changed/);
  assert.equal(disconnectCopy("zepto").title, "Disconnect Zepto?");
});
