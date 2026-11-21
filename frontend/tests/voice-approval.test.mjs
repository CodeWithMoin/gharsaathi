import assert from "node:assert/strict";
import test from "node:test";
import { voiceApprovalFromToolEvent } from "../app/voice-approval.mjs";

const money = (paise) => ({ paise, display: `₹${paise / 100}` });
const request = {
  approval: { id: "apr-1", basket_id: "b-1", basket_revision: 2, status: "pending", fingerprint: "fp", payable: money(19900), created_at: "2026-11-22T10:00:00Z", expires_at: "2026-11-22T10:10:00Z", decided_at: null },
  quote: { item_total: money(18900), charges: [{ label: "Delivery", amount: money(1000) }], offers: [], payable: money(19900), eta_text: "10 min", quoted_at: "2026-11-22T10:00:00Z" },
  basket: { id: "b-1", provider: "zepto", status: "awaiting_approval", revision: 2, lines: [], item_total: money(18900), savings_vs_mrp: money(0) },
  item_total_matches: true,
};
const event = (overrides = {}) => ({ type: "basket.approval_requested", provider: "zepto", store: "Zepto", approval: { payable: "₹199" }, quote: {}, request, ...overrides });

test("hands the exact voice-requested bill to the basket panel", () => {
  assert.deepEqual(voiceApprovalFromToolEvent(event()), { provider: "zepto", request });
});

test("ignores events without a usable request instead of showing a broken bill", () => {
  assert.equal(voiceApprovalFromToolEvent(event({ request: undefined })), null);
  assert.equal(voiceApprovalFromToolEvent(event({ provider: "swiggy_instamart" })), null);
  assert.equal(voiceApprovalFromToolEvent(event({ request: { ...request, approval: { ...request.approval, payable: "₹199" } } })), null);
  assert.equal(voiceApprovalFromToolEvent(event({ type: "basket.updated" })), null);
  assert.equal(voiceApprovalFromToolEvent(null), null);
});
