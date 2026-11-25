import assert from "node:assert/strict";
import test from "node:test";
import { liveRiderHelpView } from "../../app/delivery-line.mjs";

const active = {
  active: "yes", provider: "zepto", order_status: "out for delivery", eta: "8:26 pm",
  delivery_address: "Flat 402, Lake View", landmark: "Opposite the blue temple",
  route_directions: "NOT_AVAILABLE", handover_instructions: "Guard ke paas chhod dijiye",
};

test("off, unreadable or missing previews hide the section", () => {
  assert.equal(liveRiderHelpView(null), null);
  assert.equal(liveRiderHelpView({ enabled: false, context: null }), null);
});

test("no active order says riders are told none is expected", () => {
  const view = liveRiderHelpView({ enabled: true, context: { active: "no", provider: "zepto" } });
  assert.equal(view?.tone, "neutral");
  assert.match(view?.detail ?? "", /no delivery is expected/);
});

test("an active order lists exactly what the rider hears, skipping NOT_AVAILABLE", () => {
  const view = liveRiderHelpView({ enabled: true, context: active });
  assert.equal(view?.tone, "success");
  assert.equal(view?.detail, "A Zepto rider calling now hears (out for delivery · arriving 8:26 pm):");
  assert.deepEqual(view?.lines.map((line) => line.label), ["Address", "Landmark", "Handover"]);
});

test("an active order without an address is a warning", () => {
  const view = liveRiderHelpView({ enabled: true, context: { ...active, delivery_address: "NOT_AVAILABLE" } });
  assert.equal(view?.tone, "warning");
  assert.deepEqual(view?.lines, []);
});
