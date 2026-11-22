import assert from "node:assert/strict";
import test from "node:test";

import { doctorCounts, formatMs, groupMemory, inspectorAllowed, shapeBaskets, shapeTurns } from "../../app/dev-inspector.mjs";

const turn = {
  turn_id: "abc123def456",
  at: "2026-11-22T10:00:00+00:00",
  channel: "typed",
  outcome: "ok",
  model: "gpt-6-luna",
  prompt_version: "1.30.0",
  total_ms: 6400,
  rounds: [
    { round: 1, model_ms: 2300, tokens: { input: 10000, output: 20 }, tools: [
      { name: "search_products", args: { query: "amul taaza 1 l" }, status: "ok", ms: 812, counts: { options: 3 }, stages: { zepto_search: 790 } },
      { name: "search_products", args: { query: "dahi" }, status: "refused_search_cap", ms: 0 },
    ] },
    { round: 2, model_ms: 3100, tools: [] },
  ],
  totals: { rounds: 2, tools: 2, model_ms: 5400, tool_ms: 812, tokens: { input: 10000, output: 20 } },
};

test("the inspector shows only outside production and only when the backend enables it", () => {
  assert.equal(inspectorAllowed("development", { enabled: true }), true);
  assert.equal(inspectorAllowed("production", { enabled: true }), false);
  assert.equal(inspectorAllowed("development", null), false);
  assert.equal(inspectorAllowed("development", { enabled: false }), false);
});

test("turns are summarised with rounds, model share, slowest tool and problems", () => {
  const [row] = shapeTurns([turn]);
  assert.equal(row.total, "6,400 ms");
  assert.equal(row.rounds, 2);
  assert.equal(row.modelShare, 84);
  assert.deepEqual(row.toolNames, ["search_products", "search_products"]);
  assert.equal(row.slowestTool, "search_products · 812 ms");
  assert.deepEqual(row.problems, ["search_products: refused_search_cap"]);
  assert.equal(row.detail[0].tools[0].args, "query=amul taaza 1 l");
  assert.deepEqual(row.detail[0].tools[0].stages, ["zepto_search 790 ms"]);
  assert.equal(row.detail[1].tools.length, 0);
});

test("empty and partial traces do not throw", () => {
  assert.deepEqual(shapeTurns(null), []);
  const [row] = shapeTurns([{ turn_id: "x", rounds: [] }]);
  assert.equal(row.total, "–");
  assert.equal(row.slowestTool, null);
  assert.equal(formatMs(12_345), "12.3 s");
});

test("baskets keep stored amount strings and a locked basket cannot be cleared", () => {
  const shaped = shapeBaskets([
    { provider: "zepto", status: "checkout_pending", revision: 4, locked: true, item_count: 2, item_total: "₹110", lines: [{ name: "Lassi", quantity: 2, unit_price: "₹55", line_total: "₹110" }] },
    { provider: "swiggy_instamart", status: "draft", revision: 1, locked: false, item_count: 0, item_total: "₹0", lines: [] },
  ]);
  assert.equal(shaped[0].summary, "2 items · ₹110 · rev 4");
  assert.equal(shaped[0].canClear, false);
  assert.equal(shaped[1].label, "Instamart");
  assert.equal(shaped[1].canClear, false);
});

test("doctor counts and memory groups", () => {
  assert.deepEqual(doctorCounts([{ status: "green" }, { status: "red" }, { status: "amber" }, { status: "green" }]), { green: 2, amber: 1, red: 1 });
  const groups = groupMemory([{ id: "1", status: "proposed", text: "a" }, { id: "2", status: "confirmed", text: "b" }]);
  assert.deepEqual(groups.map((g) => g.status), ["confirmed", "proposed"]);
});
