import assert from "node:assert/strict";
import test from "node:test";
import { applyAssistantEvent } from "../../app/assistant-events.mjs";

function recorder() {
  const calls = [];
  return {
    calls,
    handlers: {
      onResults: (query, products, item) => calls.push(["results", query, products.length, item]),
      onBasketTouched: (provider) => calls.push(["basket", provider]),
      onApprovalRequested: (provider, request) => calls.push(["approval", provider, request.approval.id]),
      onAddressForm: (request) => calls.push(["address", request]),
    },
  };
}

const money = (paise) => ({ paise, display: `₹${paise / 100}` });
const approvalRequest = {
  approval: { id: "apr-1", fingerprint: "fp", payable: money(19900), expires_at: "2026-11-22T10:10:00Z" },
  quote: { item_total: money(18900), charges: [], payable: money(19900) },
  basket: { provider: "swiggy_instamart", lines: [] },
};

test("search results reach the results handler", () => {
  const { calls, handlers } = recorder();
  const outcome = applyAssistantEvent({ type: "tool.results", query: "AXE Dark", store: "Zepto", products: [{}, {}] }, handlers);
  assert.equal(outcome.kind, "results");
  assert.deepEqual(calls, [["results", "AXE Dark", 2, { itemKey: undefined, itemLabel: undefined }]]);
});

test("search results carry the item key and label for per-item result groups", () => {
  const { calls, handlers } = recorder();
  applyAssistantEvent({ type: "tool.results", query: "atta 5 kg", store: "Zepto", products: [{}], item_key: "atta", item_label: "Atta" }, handlers);
  applyAssistantEvent({ type: "tool.results", query: "dahi", store: "Zepto", products: [], item_key: 7, item_label: "" }, handlers);
  assert.deepEqual(calls, [
    ["results", "atta 5 kg", 1, { itemKey: "atta", itemLabel: "Atta" }],
    ["results", "dahi", 0, { itemKey: undefined, itemLabel: undefined }],
  ]);
});

test("basket updates name the provider and summarise the basket", () => {
  const { calls, handlers } = recorder();
  const outcome = applyAssistantEvent(
    { type: "basket.updated", provider: "zepto", basket: { item_count: 2, item_total: "₹398", store: "Zepto" } },
    handlers,
  );
  assert.deepEqual(calls, [["basket", "zepto"]]);
  assert.equal(outcome.notice, "Zepto · 2 items · ₹398");
  assert.equal(outcome.message, "Basket update ho gaya.");
});

test("an unknown provider is not forwarded", () => {
  const { calls, handlers } = recorder();
  applyAssistantEvent({ type: "basket.updated", provider: "bigbasket", basket: { item_count: 0 } }, handlers);
  assert.deepEqual(calls, [["basket", undefined]]);
});

test("a requested bill hands the exact approval request to the basket", () => {
  const { calls, handlers } = recorder();
  const outcome = applyAssistantEvent(
    { type: "basket.approval_requested", provider: "swiggy_instamart", store: "Swiggy Instamart", approval: { payable: "₹199" }, quote: {}, request: approvalRequest },
    handlers,
  );
  assert.deepEqual(calls, [["approval", "swiggy_instamart", "apr-1"], ["basket", "swiggy_instamart"]]);
  assert.equal(outcome.notice, "Swiggy Instamart · Final bill · ₹199");
});

test("a bill event without a usable request still opens the basket but no approval", () => {
  const { calls, handlers } = recorder();
  applyAssistantEvent({ type: "basket.approval_requested", provider: "zepto", approval: { payable: "₹1" } }, handlers);
  assert.deepEqual(calls, [["basket", "zepto"]]);
});

test("address form prefill is sanitised and unavailable reasons are explained", () => {
  const { calls, handlers } = recorder();
  applyAssistantEvent({ type: "address_form.open", provider: "zepto", prefill: { flat_details: " 302 ", phone: "999", type: "PALACE" } }, handlers);
  const unavailable = applyAssistantEvent({ type: "address_form.unavailable", reason: "demo_mode" }, handlers);
  assert.deepEqual(calls, [["address", { prefill: { flat_details: "302" } }], ["address", { unavailable: "demo_mode" }]]);
  assert.equal(unavailable.message, "Demo mode mein naya address add nahi hota.");
});

test("errors and not-connected only return a status", () => {
  const { calls, handlers } = recorder();
  assert.equal(applyAssistantEvent({ type: "tool.error", message: "Swiggy search failed." }, handlers).message, "Swiggy search failed.");
  assert.equal(applyAssistantEvent({ type: "tool.not_connected", query: "atta" }, handlers).kind, "not_connected");
  assert.deepEqual(calls, []);
});

test("malformed or unknown events are ignored", () => {
  const { calls, handlers } = recorder();
  for (const event of [null, "tool.results", [], { type: "tool.results" }, { type: "tool.started", name: "x" }, { type: "address_form.open", provider: "swiggy_instamart" }]) {
    assert.equal(applyAssistantEvent(event, handlers), null);
  }
  assert.deepEqual(calls, []);
});
