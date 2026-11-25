import assert from "node:assert/strict";
import test from "node:test";
import { FORWARDING_OFF_CODE, deliveryLineSettingsView, formatDeliveryNumber, forwardingCode } from "../../app/delivery-line-settings.mjs";

test("the AI number is shown in Indian grouping", () => {
  assert.equal(formatDeliveryNumber("+919999999999"), "+91 99 9999 9999");
  assert.equal(formatDeliveryNumber("099 9999 9999"), "099 9999 9999");
});

test("forwarding is when-busy only (**67*) and turns off with ##002#", () => {
  assert.equal(forwardingCode("+919999999999"), "**67*+919999999999#");
  assert.equal(forwardingCode(""), "");
  assert.equal(FORWARDING_OFF_CODE, "##002#");
  const view = deliveryLineSettingsView({ enabled: true, inbound_phone_number: "+919999999999" });
  assert.ok(!/\*\*61\*|\*\*62\*/.test(view.forwarding), "never unanswered/unreachable forwarding");
});

test("status follows the server config", () => {
  assert.equal(deliveryLineSettingsView(undefined).status, "Checking…");
  assert.equal(deliveryLineSettingsView(null).status, "Couldn't check right now");
  assert.deepEqual([deliveryLineSettingsView({ enabled: true, inbound_phone_number: null }).status, deliveryLineSettingsView({ enabled: true, inbound_phone_number: null }).ready], ["On", true]);
  assert.equal(deliveryLineSettingsView({ enabled: false, inbound_phone_number: null }).ready, false);
  assert.equal(deliveryLineSettingsView({ enabled: false, inbound_phone_number: null }).display, "Not configured");
});
