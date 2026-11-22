import assert from "node:assert/strict";
import test from "node:test";
import { coverageText, fullResyncAvailability, importSummaryLine } from "../../app/history-import-summary.mjs";

test("Swiggy history is labelled as the last 15 days", () => {
  assert.equal(coverageText("swiggy", { coverage: "last_15_days" }), "Swiggy Instamart orders from the last 15 days");
  assert.equal(
    importSummaryLine("swiggy", { cached: false, imported_orders: 1, coverage: "last_15_days", complete: true }),
    "Checked Swiggy Instamart orders from the last 15 days; added 1 new order.",
  );
});

test("Zepto history shows the month window it covers", () => {
  const fields = { coverage: "full_history", window_start: "2026-06-03T10:00:00Z", window_end: "2026-11-20T10:00:00Z" };
  assert.equal(coverageText("zepto", fields), "Zepto orders from Jun 2026 to Nov 2026");
  assert.equal(coverageText("zepto", { ...fields, window_start: fields.window_end }), "Zepto orders from Nov 2026");
  assert.equal(coverageText("zepto", { coverage: "full_history" }), "Zepto order history");
});

test("a partial import says older orders come next time", () => {
  const line = importSummaryLine("zepto", { cached: false, imported_orders: 30, complete: false, stopped_reason: "detail_budget", detail_failures: 1, coverage: "full_history", window_start: "2026-06-03T10:00:00Z", window_end: "2026-11-20T10:00:00Z" });
  assert.match(line, /^Checked Zepto orders from Jun 2026 to Nov 2026; added 30 new orders\./);
  assert.match(line, /next refresh/);
  assert.match(line, /1 order could not be read/);
  assert.match(importSummaryLine("zepto", { cached: false, imported_orders: 2, complete: false, stopped_reason: "rate_limited" }), /slow down/);
  assert.match(importSummaryLine("zepto", { cached: true, imported_orders: 0 }), /checked recently/);
});

test("full re-import shows when it is available again", () => {
  const now = new Date("2026-11-22T12:00:00Z");
  assert.deepEqual(fullResyncAvailability(null, now), { available: true, label: "" });
  assert.deepEqual(fullResyncAvailability({ full_resync_available_at: "2026-11-22T11:00:00Z" }, now), { available: true, label: "" });
  const later = fullResyncAvailability({ full_resync_available_at: "2026-11-23T10:30:00Z" }, now);
  assert.equal(later.available, false);
  assert.match(later.label, /^Available again 23 Nov/);
});
