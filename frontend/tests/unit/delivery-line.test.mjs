import assert from "node:assert/strict";
import test from "node:test";
import { DELIVERY_LINE_NUMBER, FORWARDING_OFF_CODE, deliveryLineView, formatDeliveryNumber, forwardingCode } from "../../app/delivery-line.mjs";

test("the AI number is shown in Indian grouping", () => {
  assert.equal(formatDeliveryNumber("+918064264240"), "+91 80 6426 4240");
  assert.equal(formatDeliveryNumber("080 6426 4240"), "080 6426 4240");
});

test("forwarding is when-busy only (**67*) and turns off with ##002#", () => {
  assert.equal(forwardingCode(DELIVERY_LINE_NUMBER), "**67*+918064264240#");
  assert.equal(FORWARDING_OFF_CODE, "##002#");
  const view = deliveryLineView({ enabled: true, inbound_phone_number: "+918064264240" });
  assert.ok(!/\*\*61\*|\*\*62\*/.test(view.forwarding), "never unanswered/unreachable forwarding");
});

test("status follows the server config", () => {
  assert.equal(deliveryLineView(undefined).status, "Checking…");
  assert.equal(deliveryLineView(null).status, "Couldn't check right now");
  assert.deepEqual([deliveryLineView({ enabled: true, inbound_phone_number: null }).status, deliveryLineView({ enabled: true, inbound_phone_number: null }).ready], ["On", true]);
  assert.equal(deliveryLineView({ enabled: false, inbound_phone_number: null }).ready, false);
  assert.equal(deliveryLineView({ enabled: true, inbound_phone_number: null }).display, "+91 80 6426 4240", "falls back to the known number");
});
