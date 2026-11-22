/**
 * Pure logic for the per-item options carousel: which options show, in what
 * order, the badge on the first card, position labels and accessible names.
 *
 * The assistant's search decides the order and sends each option's `position`;
 * the model refers to options by the same number, so cards are shown in that
 * order and numbered with it, never re-sorted here.
 *
 * Products without `position` (older saved chats, typed /api search) use the
 * fallback: remembered first, then preferred, then the backend's order;
 * unavailable last; avoided hidden.
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

export const PHOTO_MATCH_BADGE = "Looks like your photo";
export const PHOTO_DIFFERENT_NOTE = "Not the pack in your photo";

/**
 * The backend's visual comparison with the user's photo (photo searches only):
 * the badge for a same-product option, or a quiet note for a different one.
 *
 * @param {{ visual_match?: unknown }} product
 * @returns {{ badge: "Looks like your photo" | null, note: string | null }}
 */
export function photoMatch(product) {
  if (product.visual_match === "same_product") return { badge: PHOTO_MATCH_BADGE, note: null };
  if (product.visual_match === "different") return { badge: null, note: PHOTO_DIFFERENT_NOTE };
  return { badge: null, note: null };
}

/**
 * Card 1's badge, or any card's photo badge. A card that looks like the user's
 * photo says so; that wins over the household badges.
 *
 * @param {{ remembered?: unknown, preferred?: unknown, available?: unknown, visual_match?: unknown }} product
 * @param {number} number
 */
function cardBadge(product, number) {
  const photo = photoMatch(product).badge;
  if (photo) return photo;
  return number === 1 ? bestPickBadge(product) : null;
}

/** @param {unknown} value */
const validPosition = (value) => (Number.isInteger(value) && /** @type {number} */ (value) >= 1 ? /** @type {number} */ (value) : null);

/**
 * @template {{ provider?: string, product_id: string, sku_id: string, name?: unknown, available?: unknown, remembered?: unknown, preferred?: unknown, avoided?: unknown, position?: unknown }} P
 * @param {P[]} products
 * @param {number} [limit]
 * @returns {{ key: string, product: P, number: number, badge: string | null }[]}
 */
export function orderOptions(products, limit = MAX_OPTIONS) {
  const named = products.filter((product) => product && text(product.name));
  if (named.length && named.every((product) => validPosition(product.position) !== null)) {
    return [...named]
      .sort((a, b) => /** @type {number} */ (validPosition(a.position)) - /** @type {number} */ (validPosition(b.position)))
      .slice(0, limit)
      .map((product) => {
        const number = /** @type {number} */ (validPosition(product.position));
        return { key: `${optionKey(product)}:${number}`, product, number, badge: cardBadge(product, number) };
      });
  }
  return fallbackOrder(named, limit);
}

/**
 * @template {{ provider?: string, product_id: string, sku_id: string, name?: unknown, available?: unknown, remembered?: unknown, preferred?: unknown, avoided?: unknown }} P
 * @param {P[]} products
 * @param {number} limit
 */
function fallbackOrder(products, limit) {
  const seen = new Set();
  /** @type {{ product: P, index: number, key: string }[]} */
  const kept = [];
  products.forEach((product, index) => {
    if (product.avoided === true) return;
    const key = optionKey(product);
    if (seen.has(key)) return;
    seen.add(key);
    kept.push({ product, index, key });
  });
  return kept
    .sort((a, b) => rank(a.product) - rank(b.product) || a.index - b.index)
    .slice(0, limit)
    .map(({ product, key }, index) => ({ key, product, number: index + 1, badge: cardBadge(product, index + 1) }));
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
