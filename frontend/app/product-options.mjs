/**
 * Pure logic for the per-item options carousel: which options show, in what
 * order, the badge on the first card, position labels and accessible names.
 *
 * Order: the household's remembered option first, then a preferred one, then
 * the rest in the backend's order (its best match first). Unavailable options
 * go last. Options the household asked to avoid are hidden.
 *
 * Money is never formatted or subtracted here: cards show the backend's
 * `price.offer_display` / `mrp_display` / `saving_display` strings.
 */

export const MAX_OPTIONS = 8;

const STORE_NAMES = { swiggy_instamart: "Swiggy", zepto: "Zepto" };

/** @param {unknown} value */
const text = (value) => (typeof value === "string" ? value.trim() : "");

/** @param {{ provider?: string, product_id: string, sku_id: string }} product */
export function optionKey(product) {
  return `${product.provider ?? "swiggy_instamart"}:${product.product_id}:${product.sku_id}`;
}

/** @param {{ remembered?: unknown, preferred?: unknown, available?: unknown }} product */
function rank(product) {
  if (!product.available) return 3;
  if (text(product.remembered)) return 0;
  if (product.preferred === true) return 1;
  return 2;
}

/**
 * @param {{ remembered?: unknown, preferred?: unknown, available?: unknown }} product
 * @returns {"You bought this before" | "Your usual" | "Best match" | null}
 */
export function bestPickBadge(product) {
  if (!product.available) return null;
  if (text(product.remembered) === "purchase_history") return "You bought this before";
  if (text(product.remembered) || product.preferred === true) return "Your usual";
  return "Best match";
}

/**
 * @template {{ provider?: string, product_id: string, sku_id: string, name?: unknown, available?: unknown, remembered?: unknown, preferred?: unknown, avoided?: unknown }} P
 * @param {P[]} products
 * @param {number} [limit]
 * @returns {{ key: string, product: P, number: number, badge: string | null }[]}
 */
export function orderOptions(products, limit = MAX_OPTIONS) {
  const seen = new Set();
  /** @type {{ product: P, index: number, key: string }[]} */
  const kept = [];
  products.forEach((product, index) => {
    if (!product || !text(product.name) || product.avoided === true) return;
    const key = optionKey(product);
    if (seen.has(key)) return;
    seen.add(key);
    kept.push({ product, index, key });
  });
  return kept
    .sort((a, b) => rank(a.product) - rank(b.product) || a.index - b.index)
    .slice(0, limit)
    .map(({ product, key }, index) => ({ key, product, number: index + 1, badge: index === 0 ? bestPickBadge(product) : null }));
}

/** "1/3" */
export function positionLabel(index, total) {
  return `${Math.min(index + 1, total)}/${total}`;
}

/** "Option 1 of 3" */
export function optionPositionLabel(number, total) {
  return `Option ${number} of ${total}`;
}

/**
 * @param {{ provider?: string }} product
 * @param {"demo" | "live"} [commerceMode]
 */
export function storeName(product, commerceMode = "live") {
  const name = STORE_NAMES[/** @type {keyof typeof STORE_NAMES} */ (product.provider ?? "swiggy_instamart")] ?? "Swiggy";
  return commerceMode === "demo" ? `${name} demo` : name;
}

/**
 * "Amul Taaza 1 L": the pack size is added when the name doesn't already say it.
 *
 * @param {{ name: string, pack_size?: string | null }} product
 */
export function optionTitle(product) {
  const name = text(product.name);
  const pack = text(product.pack_size);
  if (!pack) return name;
  const squash = (value) => value.toLocaleLowerCase().replace(/\s+/g, "");
  return squash(name).includes(squash(pack)) ? name : `${name} ${pack}`;
}

/**
 * @param {{ name: string, pack_size?: string | null, provider?: string }} product
 * @param {"add" | "increase" | "decrease" | "remove"} action
 */
export function basketActionLabel(product, action) {
  const title = optionTitle(product);
  const store = storeName(product);
  if (action === "add") return `Add ${title} to ${store} basket`;
  if (action === "remove") return `Remove ${title} from ${store} basket`;
  return `${action === "increase" ? "Increase" : "Decrease"} quantity of ${title} in ${store} basket`;
}

/**
 * The backend's ₹ strings for one option, or null where it sent none.
 *
 * @param {{ price?: { offer_display?: unknown, mrp_display?: unknown, saving_display?: unknown } }} product
 */
export function optionPrices(product) {
  const price = product.price ?? {};
  return {
    price: text(price.offer_display) || null,
    mrp: text(price.mrp_display) || null,
    saving: text(price.saving_display) || null,
  };
}

/**
 * Quantity of this option in its store's basket (0 when absent).
 *
 * @param {Partial<Record<string, { lines: { ref: { provider: string, variant_id: string }, quantity: number }[] } | undefined>>} baskets
 * @param {{ provider?: string, spin_id?: string }} product
 */
export function basketQuantity(baskets, product) {
  const provider = product.provider ?? "swiggy_instamart";
  if (!product.spin_id) return 0;
  const line = baskets[provider]?.lines.find((item) => item.ref.provider === provider && item.ref.variant_id === product.spin_id);
  return line?.quantity ?? 0;
}

/**
 * Index of the card nearest the scroller's start edge. At the end of the
 * scroll range the last card counts as current, even if it can't reach the
 * start edge.
 *
 * @param {number[]} offsets each card's left offset inside the scroller
 * @param {number} scrollLeft
 * @param {number} maxScroll scrollWidth - clientWidth
 */
export function activeIndexFromScroll(offsets, scrollLeft, maxScroll) {
  if (!offsets.length) return 0;
  if (maxScroll > 0 && scrollLeft >= maxScroll - 2) return offsets.length - 1;
  let best = 0;
  offsets.forEach((offset, index) => {
    if (Math.abs(offset - offsets[0] - scrollLeft) < Math.abs(offsets[best] - offsets[0] - scrollLeft)) best = index;
  });
  return best;
}
