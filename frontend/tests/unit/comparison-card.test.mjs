import assert from "node:assert/strict";
import test from "node:test";
import { applyAssistantEvent } from "../../app/assistant-events.mjs";
import { comparisonFromEvent, unitPriceText, withComparisonCard } from "../../app/comparison-card.mjs";

const product = (provider, sku, available = true) => ({ provider, product_id: sku, sku_id: sku, spin_id: `sp-${sku}`, name: "Amul Sweet Lassi", pack_size: "200 ml", available, price: { mrp: 20, offer_price: 20, offer_display: "₹20" } });
const event = {
  type: "comparison.ready", item_label: "Amul lassi", aspect: "price", headline: "Zepto is ₹5 cheaper",
  options: [
    { product: product("swiggy_instamart", "a"), label: "Swiggy Instamart", unit_price: "₹12.50 per 100 ml", badges: [] },
    { product: product("zepto", "b"), label: "Zepto", unit_price: "₹10 per 100 ml", badges: ["Cheapest", "Best value per 100 ml", "<script>"] },
    { product: { name: "bad" }, badges: [] },
  ],
};

test("a comparison event becomes a card with backend strings and known badges only", () => {
  const card = comparisonFromEvent(event, 1);
  assert.equal(card.columns.length, 2);
  assert.equal(card.headline, "Zepto is ₹5 cheaper");
  assert.deepEqual(card.columns[1].badges, ["Cheapest", "Best value per 100 ml"]);
  assert.equal(unitPriceText({ unitPrice: "unit price not comparable" }), "Unit price not comparable");
});

test("fewer than two usable options or a wrong type is ignored", () => {
  assert.equal(comparisonFromEvent({ ...event, options: event.options.slice(0, 1) }), null);
  assert.equal(comparisonFromEvent({ ...event, type: "tool.results" }), null);
  assert.equal(applyAssistantEvent({ type: "comparison.ready", options: "x" }, {}), null);
});

test("the one live comparison card sits after its anchor message's cards", () => {
  let shown = null;
  const outcome = applyAssistantEvent(event, { onComparison: (comparison) => { shown = comparison; } });
  assert.equal(outcome.kind, "comparison");
  const items = [{ kind: "message", message: { id: "m1" } }, { kind: "results" }, { kind: "message", message: { id: "m2" } }];
  const placed = withComparisonCard(items, { comparison: shown, afterMessageId: "m1" });
  assert.deepEqual(placed.map((item) => item.kind), ["message", "results", "comparison", "message"]);
  assert.equal(withComparisonCard(items, null), items);
});
