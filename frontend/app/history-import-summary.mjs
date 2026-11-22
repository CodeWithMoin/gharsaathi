// Plain-language summaries for the purchase-history import. Pure; no I/O.

const LABELS = { swiggy: "Swiggy Instamart", zepto: "Zepto" };
const MONTH = new Intl.DateTimeFormat("en-IN", { month: "short", year: "numeric", timeZone: "Asia/Kolkata" });
const WHEN = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit", timeZone: "Asia/Kolkata" });

export const SWIGGY_HISTORY_DAYS = 15;

/** What a store's history import covers, e.g. "Zepto orders from Jun 2026 to Nov 2026". */
export function coverageText(provider, { coverage, window_start: start, window_end: end } = {}) {
  const label = LABELS[provider] ?? provider;
  if (coverage === "last_15_days" || provider === "swiggy") return `${label} orders from the last ${SWIGGY_HISTORY_DAYS} days`;
  if (!start || !end) return `${label} order history`;
  const from = MONTH.format(new Date(start));
  const to = MONTH.format(new Date(end));
  return from === to ? `${label} orders from ${from}` : `${label} orders from ${from} to ${to}`;
}

/** One line per store for the import result. */
export function importSummaryLine(provider, result) {
  if (result.cached) return `${LABELS[provider] ?? provider}: checked recently, not fetched again.`;
  const added = `added ${result.imported_orders} new order${result.imported_orders === 1 ? "" : "s"}`;
  const more = result.complete === false
    ? result.stopped_reason === "rate_limited" ? " The store asked us to slow down; the rest comes next time." : " Older orders will be read on the next refresh."
    : "";
  const failed = result.detail_failures ? ` ${result.detail_failures} order${result.detail_failures === 1 ? "" : "s"} could not be read.` : "";
  return `Checked ${coverageText(provider, result)}; ${added}.${more}${failed}`;
}

/** Whether "Re-import full history" can run now, and if not, when it can. */
export function fullResyncAvailability(status, now = new Date()) {
  const at = status?.full_resync_available_at;
  if (!at || Date.parse(at) <= now.getTime()) return { available: true, label: "" };
  return { available: false, label: `Available again ${WHEN.format(new Date(at))}` };
}
