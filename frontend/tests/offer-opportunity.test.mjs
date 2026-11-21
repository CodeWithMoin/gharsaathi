import assert from "node:assert/strict";
import test from "node:test";

import { offerOpportunityCopy, visibleOfferOpportunities } from "../app/basket/offer-opportunity.mjs";

test("free-delivery copy shows threshold, saving, and true net extra", () => {
  const copy = offerOpportunityCopy({
    kind: "free_delivery",
    title: "Free delivery",
    minimum_order_paise: 9900,
    remaining_paise: 7200,
    potential_saving_paise: 3000,
    net_extra_paise: 4200,
    unlocked: false,
    applied: false,
    provider_confirmed: true,
  });
  assert.match(copy.title, /₹72/);
  assert.match(copy.detail, /₹30/);
  assert.match(copy.detail, /net extra ₹42/);
});

test("locked bank offer never claims an unknown discount", () => {
  const copy = offerOpportunityCopy({
    kind: "bank_offer",
    title: "Card offer",
    minimum_order_paise: 9900,
    remaining_paise: 3700,
    potential_saving_paise: null,
    net_extra_paise: null,
    unlocked: false,
    applied: false,
    provider_confirmed: false,
  });
  assert.match(copy.detail, /not confirmed/i);
  assert.doesNotMatch(copy.detail, /save ₹/i);
});

test("visible offers prefer applied and free delivery and remove duplicates", () => {
  const base = { minimum_order_paise: 9900, remaining_paise: 1000, potential_saving_paise: null, net_extra_paise: null, unlocked: false, applied: false, provider_confirmed: true };
  const visible = visibleOfferOpportunities([
    { ...base, kind: "bank_offer", title: "Card offer" },
    { ...base, kind: "bank_offer", title: "Card offer" },
    { ...base, kind: "free_delivery", title: "Free delivery" },
    { ...base, kind: "promo", title: "Applied promo", applied: true },
    { ...base, kind: "promo", title: "Another promo" },
  ]);
  assert.equal(visible.length, 3);
  assert.equal(visible[0].title, "Applied promo");
  assert.equal(visible[1].kind, "free_delivery");
});
