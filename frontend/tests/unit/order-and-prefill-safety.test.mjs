import assert from "node:assert/strict";
import test from "node:test";

import { offerOpportunityCopy, visibleOfferOpportunities } from "../../app/basket/offer-opportunity.mjs";
import { canConfirmZeptoCodOrder, checkoutStatusCopy } from "../../app/basket/zepto-checkout-state.mjs";
import { sanitizeAddressPrefill } from "../../app/voice-address-prefill.mjs";

test("the COD order button needs exactly an approved live Zepto bill", () => {
  for (const approvalStatus of ["pending", "consuming", "consumed", "superseded", "APPROVED", undefined]) {
    assert.equal(canConfirmZeptoCodOrder({ provider: "zepto", commerceMode: "live", approvalStatus }), false, String(approvalStatus));
  }
  assert.equal(canConfirmZeptoCodOrder({}), false);
  assert.equal(canConfirmZeptoCodOrder({ provider: "zepto", commerceMode: "live", approvalStatus: "approved" }), true);
});

test("every checkout status has copy and none invite a blind retry", () => {
  assert.match(checkoutStatusCopy({ status: "dispatching" }), /do not close this page or submit again/i);
  assert.match(checkoutStatusCopy({ status: "confirmed", provider_order_id: null }), /order history/i);
  assert.match(checkoutStatusCopy({ status: "rejected" }), /Nothing has been confirmed/);
  assert.equal(checkoutStatusCopy({ status: "mystery", message: "Server said no." }), "Server said no.");
  assert.match(checkoutStatusCopy({ status: "mystery" }), /Do not retry/);
});

test("voice address prefill cannot pollute prototypes and strips control characters", () => {
  const raw = JSON.parse('{"__proto__": {"polluted": true}, "constructor": "x", "name": "Ghar\\u0007\\u0000 ", "landmark": "Near\\tpark"}');
  const prefill = sanitizeAddressPrefill(raw);
  assert.deepEqual(prefill, { name: "Ghar", landmark: "Near park" });
  assert.equal({}.polluted, undefined);
  assert.equal(Object.getPrototypeOf(prefill), Object.prototype);
});

test("address type matching is exact, so lowercase model output is dropped", () => {
  assert.deepEqual(sanitizeAddressPrefill({ type: "home", name: "Flat" }), { name: "Flat" });
  assert.deepEqual(sanitizeAddressPrefill({ type: " OTHER " }), { type: "OTHER" });
});

const offer = (overrides) => ({
  kind: "free_delivery",
  title: "Free delivery",
  minimum_order_paise: 19900,
  remaining_paise: 2000,
  potential_saving_paise: 3000,
  net_extra_paise: null,
  unlocked: false,
  applied: false,
  provider_confirmed: true,
  ...overrides,
});

test("derived net extra is never negative when the saving exceeds the gap", () => {
  const copy = offerOpportunityCopy(offer({}));
  assert.match(copy.title, /₹20 more can unlock free delivery/);
  assert.match(copy.detail, /net extra ₹0 only if you add something useful/);
  assert.match(offerOpportunityCopy(offer({ remaining_paise: 4550 })).detail, /net extra ₹15\.50/);
});

test("unlocked or applied offers do not ask for more items", () => {
  assert.equal(offerOpportunityCopy(offer({ unlocked: true, remaining_paise: 0 })).title, "Free delivery unlocked");
  const applied = offerOpportunityCopy(offer({ kind: "promo", title: "FLAT50", applied: true }));
  assert.deepEqual(applied, { tone: "success", title: "FLAT50", detail: "Applied in the final bill above." });
  const met = offerOpportunityCopy(offer({ kind: "bank_offer", title: "Card offer", remaining_paise: 0 }));
  assert.doesNotMatch(met.title, /more/);
});

test("visible offers hide met non-delivery offers, sort by gap, cap at three and keep input intact", () => {
  const input = [
    offer({ kind: "promo", title: "Far promo", remaining_paise: 9000 }),
    offer({ kind: "bank_offer", title: "Met card offer", remaining_paise: 0 }),
    offer({ kind: "promo", title: "Near promo", remaining_paise: 500 }),
    offer({ kind: "promo", title: "Mid promo", remaining_paise: 3000 }),
    offer({ title: "Free delivery", remaining_paise: 4000 }),
  ];
  const snapshot = structuredClone(input);
  const visible = visibleOfferOpportunities(input);
  assert.deepEqual(visible.map((item) => item.title), ["Free delivery", "Near promo", "Mid promo"]);
  assert.deepEqual(input, snapshot);
});
