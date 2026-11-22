import assert from "node:assert/strict";
import test from "node:test";
import {
  HANDOVER_CHIPS,
  MAX_STEPS,
  addStep,
  deliveryLineView,
  directionsDirty,
  directionsDraft,
  directionsError,
  directionsPayload,
  emptyDirectionsDraft,
  pickHandover,
  removeStep,
  stepsSummary,
  updateStep,
} from "../../app/delivery-line.mjs";

const config = { enabled: true, reason: null, inbound_phone_number: "+918064264240", approved_caller_hint: "•••• 3210", route_directions_enabled: false };
const address = { id: "z-home", label: "Home", text: "Flat 402, Lake View Apartments, HSR Layout" };
const saved = { provider: "zepto", address_id: "z-home", landmark: "Opposite the park", route_directions: ["Main road pe left", "Doosri gali"], gate_note: null, handover_instructions: "Guard ke paas chhod dijiye" };
const line = (overrides = {}) => ({ phase: "none", provider: null, address_id: null, cohort_id: null, monitor_id: null, reason: null, error: null, auto_share: false, inbound_phone_number: "+918064264240", updated_at: null, ...overrides });
const view = (overrides = {}) => deliveryLineView({ config, address, recipientName: "Moin", directions: saved, line: line(), store: "Zepto", ...overrides });

test("the draft starts with one empty step and round-trips saved directions", () => {
  assert.deepEqual(emptyDirectionsDraft(), { landmark: "", steps: [""], gate_note: "", handover_instructions: "" });
  const draft = directionsDraft(saved);
  assert.deepEqual(draft.steps, ["Main road pe left", "Doosri gali"]);
  assert.deepEqual(directionsPayload(draft, "zepto", "z-home"), { ...saved });
  assert.equal(directionsDirty(draft, directionsDraft(saved)), false);
  assert.equal(directionsDirty(updateStep(draft, 1, "Teesri gali"), draft), true);
});

test("steps are capped at eight; removing the last one leaves an empty step", () => {
  let draft = emptyDirectionsDraft();
  for (let index = 0; index < 12; index += 1) draft = addStep(draft);
  assert.equal(draft.steps.length, MAX_STEPS);
  assert.deepEqual(removeStep({ ...draft, steps: ["only"] }, 0).steps, [""]);
});

test("the payload trims, drops empty steps and sends null for empty optional notes", () => {
  const draft = { landmark: "  Opposite   park ", steps: [" Main road ", "", "  "], gate_note: " ", handover_instructions: "" };
  assert.deepEqual(directionsPayload(draft, "swiggy", "s1"), { provider: "swiggy", address_id: "s1", landmark: "Opposite park", route_directions: ["Main road"], gate_note: null, handover_instructions: null });
});

test("handover chips fill the field and a second tap clears it", () => {
  const chip = HANDOVER_CHIPS[2];
  assert.equal(chip, "Neeche aake call kijiye — main aa jaunga");
  const picked = pickHandover(emptyDirectionsDraft(), chip);
  assert.equal(picked.handover_instructions, chip);
  assert.equal(pickHandover(picked, chip).handover_instructions, "");
  assert.equal(pickHandover(picked, HANDOVER_CHIPS[0]).handover_instructions, HANDOVER_CHIPS[0]);
});

test("the draft refuses a missing landmark, long steps, secrets and links", () => {
  const base = directionsDraft(saved);
  assert.equal(directionsError(base), null);
  assert.match(directionsError({ ...base, landmark: " a " }), /landmark/);
  assert.match(directionsError({ ...base, steps: ["x".repeat(81)] }), /step/);
  assert.match(directionsError({ ...base, handover_instructions: "x".repeat(101) }), /handover/);
  assert.match(directionsError({ ...base, gate_note: "Gate OTP 4821" }), /OTP/);
  assert.match(directionsError({ ...base, landmark: "See www.example.test" }), /links/);
  assert.match(directionsError({ ...base, landmark: "Park\nnext" }), /one line/);
});

test("the panel is off, with the reason, when the server or owner isn't set", () => {
  assert.equal(view({ config: null }).kind, "loading");
  assert.match(view({ config: { ...config, enabled: false, reason: "not_owner" } }).message, /owner/);
  assert.match(view({ config: { ...config, enabled: false, reason: "not_configured" } }).message, /not set up/);
});

test("sharing waits for an address, a name and directions", () => {
  assert.equal(view().canShare, true);
  assert.deepEqual([view({ address: null }).canShare, view({ address: null }).message], [false, "Choose a delivery address first."]);
  assert.equal(view({ recipientName: " " }).message, "Add the recipient's name first.");
  assert.equal(view({ directions: null }).message, "Add directions for this address first.");
});

test("ready shows only for the selected address, with the number and the store phrase", () => {
  const ready = view({ line: line({ phase: "ready", provider: "zepto", address_id: "z-home" }) });
  assert.equal(ready.kind, "ready");
  assert.match(ready.message, /•••• 3210 can call \+918064264240 and say “Sir, Zepto\.”/);
  assert.equal(view({ line: line({ phase: "ready", provider: "zepto", address_id: "z-office" }) }).kind, "idle");
});

test("processing, rejected, failed and unknown are never shown as ready", () => {
  const at = { provider: "zepto", address_id: "z-home" };
  assert.equal(view({ line: line({ phase: "processing", ...at }) }).kind, "processing");
  assert.equal(view({ line: line({ phase: "rejected", ...at }) }).tone, "danger");
  assert.equal(view({ line: line({ phase: "failed", ...at }) }).tone, "danger");
  const unknown = view({ line: line({ phase: "unknown", ...at }) });
  assert.equal(unknown.kind, "unknown");
  assert.equal(unknown.canShare, false);
});

test("a cleared line says so and why; a failed clear blocks sharing", () => {
  const cleared = view({ line: line({ phase: "cleared", reason: "address_changed" }) });
  assert.equal(cleared.message, "Delivery line cleared. You changed the delivery address.");
  assert.equal(cleared.canShare, true);
  assert.equal(view({ line: line({ phase: "cleared", reason: "delivered" }) }).message, "Delivery line cleared. The order was delivered.");
  assert.equal(view({ line: line({ phase: "cleared", reason: "user_cleared" }) }).message, "Delivery line cleared.");
  const failed = view({ line: line({ phase: "clear_failed" }) });
  assert.equal(failed.canShare, false);
  assert.equal(failed.tone, "danger");
});

test("saved steps are listed in order", () => {
  assert.deepEqual(stepsSummary(saved), ["1. Main road pe left", "2. Doosri gali"]);
  assert.deepEqual(stepsSummary(null), []);
});
