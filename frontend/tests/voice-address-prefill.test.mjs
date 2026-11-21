import assert from "node:assert/strict";
import test from "node:test";
import { sanitizeAddressPrefill } from "../app/voice-address-prefill.mjs";

test("keeps known text fields and trims whitespace", () => {
  assert.deepEqual(
    sanitizeAddressPrefill({ type: "WORK", name: "  Office ", flat_details: "Flat\n4B", landmark: "मंदिर के पास" }),
    { type: "WORK", name: "Office", flat_details: "Flat 4B", landmark: "मंदिर के पास" },
  );
});

test("drops coordinates, phone numbers, unknown keys and non-strings", () => {
  assert.deepEqual(
    sanitizeAddressPrefill({ latitude: "12.9", longitude: 77.5, contact_number: "9876543210", script: "<x>", name: 42, building_name: "" }),
    {},
  );
});

test("rejects unknown address types and truncates long text", () => {
  const result = sanitizeAddressPrefill({ type: "VILLA", floor: "9".repeat(80) });
  assert.equal(result.type, undefined);
  assert.equal(result.floor.length, 40);
});

test("ignores malformed payloads", () => {
  assert.deepEqual(sanitizeAddressPrefill(null), {});
  assert.deepEqual(sanitizeAddressPrefill("Flat 4B"), {});
  assert.deepEqual(sanitizeAddressPrefill(["Flat 4B"]), {});
});
