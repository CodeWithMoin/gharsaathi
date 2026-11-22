import assert from "node:assert/strict";
import test from "node:test";
import { zeptoAddressSelection } from "../../app/zepto-address-refresh.mjs";
import { applyAssistantEvent, ADDRESS_REMOVED_MESSAGE } from "../../app/assistant-events.mjs";

const list = (ids, extra = {}) => ({ addresses: ids.map((id) => ({ id })), resolution: {}, ...extra });

test("a removed selection is never replaced, even by the only address left", () => {
  assert.deepEqual(zeptoAddressSelection(list(["new"], { selection_removed: true }), "old"), { selectedId: "", selectionRemoved: true });
  assert.deepEqual(zeptoAddressSelection(list(["new"]), "old"), { selectedId: "", selectionRemoved: true });
});

test("the saved selection and first-load single address still apply", () => {
  assert.deepEqual(zeptoAddressSelection(list(["a", "b"], { resolution: { default_address_id: "b" } }), ""), { selectedId: "b", selectionRemoved: false });
  assert.deepEqual(zeptoAddressSelection(list(["a"]), ""), { selectedId: "a", selectionRemoved: false });
  assert.deepEqual(zeptoAddressSelection(list(["a", "b"]), "a"), { selectedId: "a", selectionRemoved: false });
});

test("address removal events open the Zepto picker", () => {
  const removed = [];
  const handlers = { onAddressRemoved: (provider) => removed.push(provider), onResults: () => {} };
  const outcome = applyAssistantEvent({ type: "address.selection_removed", provider: "zepto" }, handlers);
  assert.equal(outcome.notice, ADDRESS_REMOVED_MESSAGE);
  applyAssistantEvent({ type: "tool.error", message: "x", address_removed: "zepto" }, handlers);
  applyAssistantEvent({ type: "tool.error", message: "x", address_removed: "blinkit" }, handlers);
  assert.deepEqual(removed, ["zepto", "zepto"]);
});
