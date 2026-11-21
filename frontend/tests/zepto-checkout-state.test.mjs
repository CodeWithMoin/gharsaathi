import assert from "node:assert/strict";
import test from "node:test";

import { canConfirmZeptoCodOrder, checkoutStatusCopy } from "../app/basket/zepto-checkout-state.mjs";

test("only an approved live Zepto bill can expose the COD order confirmation", () => {
  assert.equal(canConfirmZeptoCodOrder({ provider: "zepto", commerceMode: "live", approvalStatus: "approved" }), true);
  assert.equal(canConfirmZeptoCodOrder({ provider: "swiggy_instamart", commerceMode: "live", approvalStatus: "approved" }), false);
  assert.equal(canConfirmZeptoCodOrder({ provider: "zepto", commerceMode: "demo", approvalStatus: "approved" }), false);
  assert.equal(canConfirmZeptoCodOrder({ provider: "zepto", commerceMode: "live", approvalStatus: "pending" }), false);
});

test("unknown checkout outcomes explicitly tell the customer not to retry", () => {
  assert.match(checkoutStatusCopy({ status: "outcome_unknown" }), /Do not retry/i);
  assert.match(checkoutStatusCopy({ status: "confirmed", provider_order_id: "ZEP-42" }), /ZEP-42/);
});
