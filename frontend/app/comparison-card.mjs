/**
 * The comparison card from a `comparison.ready` assistant event (compare_options).
 *
 * Every amount, unit price, difference and badge is a backend string; this
 * module only validates the untrusted event and shapes it for the card. The
 * chat shows at most one live comparison card: a new one replaces the old one
 * at the newest point. It is not saved with the chat.
 */

const PROVIDERS = new Set(["swiggy_instamart", "zepto"]);
const BADGES = /^(Cheapest|Best value per (100 g|100 ml|piece))$/;
export const MAX_COLUMNS = 4;

/** @param {unknown} value @param {number} [max] */
const text = (value, max = 160) => (typeof value === "string" ? value.trim().slice(0, max) : "");

/**
 * @typedef {{ product: Record<string, any> & { name: string, provider: "swiggy_instamart" | "zepto", available: boolean }, label: string, unitPrice: string, badges: string[] }} ComparisonColumn
 * @typedef {{ id: string, itemLabel: string, aspect: string, headline: string, columns: ComparisonColumn[] }} Comparison
 * @typedef {{ comparison: Comparison, afterMessageId: string | null }} LiveComparisonCard
 */

/**
 * @param {unknown} raw
 * @returns {ComparisonColumn | null}
 */
function columnFrom(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const entry = /** @type {Record<string, any>} */ (raw);
  const product = entry.product;
  if (!product || typeof product !== "object" || Array.isArray(product)) return null;
  if (!PROVIDERS.has(product.provider) || !text(product.name) || typeof product.product_id !== "string" || typeof product.sku_id !== "string") return null;
  if (!product.price || typeof product.price !== "object") return null;
  const badges = Array.isArray(entry.badges) ? entry.badges.filter((badge) => typeof badge === "string" && BADGES.test(badge)) : [];
  return {
    product: { ...product, available: product.available === true },
    label: text(entry.label) || text(product.name),
    unitPrice: text(entry.unit_price, 60),
    badges: [...new Set(badges)],
  };
}

/**
 * The card for a `comparison.ready` event, or null when it is malformed or has
 * fewer than two usable options.
 *
 * @param {unknown} raw
 * @param {number} [now]
 * @returns {Comparison | null}
 */
export function comparisonFromEvent(raw, now = Date.now()) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const event = /** @type {Record<string, any>} */ (raw);
  if (event.type !== "comparison.ready" || !Array.isArray(event.options)) return null;
  const columns = event.options.slice(0, MAX_COLUMNS).map(columnFrom).filter((column) => column !== null);
  if (columns.length < 2) return null;
  return {
    id: `comparison-${now.toString(36)}-${columns.map((column) => column.product.sku_id).join("-").slice(0, 80)}`,
    itemLabel: text(event.item_label, 120),
    aspect: text(event.aspect, 20) || "all",
    headline: text(event.headline),
    columns,
  };
}

/** @param {Comparison} comparison */
export function comparisonTitle(comparison) {
  return comparison.itemLabel ? `Compare · ${comparison.itemLabel}` : "Compare";
}

/**
 * "Not comparable" for the unit-price row when the backend could not compare it.
 *
 * @param {ComparisonColumn} column
 */
export function unitPriceText(column) {
  return !column.unitPrice || column.unitPrice === "unit price not comparable" ? "Unit price not comparable" : column.unitPrice;
}

/**
 * Inserts the live comparison card after its anchor message and the cards that
 * already follow it; without a (known) anchor it goes last.
 *
 * @template {{ kind: string, message?: { id: string } }} T
 * @param {T[]} items
 * @param {LiveComparisonCard | null} card
 * @returns {(T | { kind: "comparison", card: LiveComparisonCard })[]}
 */
export function withComparisonCard(items, card) {
  if (!card) return items;
  const start = card.afterMessageId === null ? -1 : items.findIndex((item) => item.kind === "message" && item.message?.id === card.afterMessageId);
  let index = items.length;
  if (start !== -1) {
    index = start + 1;
    while (index < items.length && items[index].kind !== "message") index += 1;
  }
  return [...items.slice(0, index), { kind: "comparison", card }, ...items.slice(index)];
}
