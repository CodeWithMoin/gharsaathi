/**
 * Which bill and order cards may still be acted on.
 *
 * A bill or an order card is bound to one basket revision. The moment that
 * store's basket changes (an edit bumps `revision`; an order closes the basket
 * and the next one has a new `id`), an open card for the old revision is out
 * of date: it collapses to a one-line summary with no buttons, and the next
 * bill or order card replaces it at the newest point in the chat.
 *
 * A card whose order may already exist (placing, outcome unknown, payment in
 * progress) is never collapsed by a basket change: only the store can settle it.
 */

/** @typedef {"swiggy_instamart" | "zepto"} Store */
/** @typedef {{ id: string, provider: Store, revision: number, status: string, lines: { quantity: number }[] }} BasketLike */
/** @typedef {{ basket: { id: string, revision: number } }} BillLike */
/**
 * @typedef {{
 *   confirmation: import("./order-confirmation.mjs").OrderConfirmation,
 *   afterMessageId: string | null,
 *   result: import("./order-confirmation.mjs").OrderCardState["status"] | null,
 *   superseded: boolean,
 * }} LiveOrderCard
 */

/** States in which the order may exist at the store: never superseded by a basket change. */
const IN_FLIGHT = new Set(["placing", "unknown", "checking_order", "awaiting_payment", "checking", "payment_succeeded", "payment_unknown", "paid_order_failed"]);
const CLOSED_BASKET = new Set(["ordered", "abandoned"]);

/**
 * True when a bill was priced for another basket or revision than the one shown.
 *
 * @param {Pick<BasketLike, "id" | "revision"> | null | undefined} basket
 * @param {BillLike | null | undefined} bill
 */
export function billIsStale(basket, bill) {
  if (!basket || !bill?.basket) return false;
  return bill.basket.id !== basket.id || bill.basket.revision !== basket.revision;
}

export const STALE_BILL_TEXT = "This bill is out of date — get a fresh bill";
export const STALE_ORDER_TEXT = "Out of date — basket changed";

/**
 * A freshly prepared order card. It replaces any earlier card at the newest point.
 *
 * @param {import("./order-confirmation.mjs").OrderConfirmation} confirmation
 * @param {string | null} afterMessageId
 * @returns {LiveOrderCard}
 */
export function newOrderCard(confirmation, afterMessageId) {
  return { confirmation, afterMessageId, result: null, superseded: false };
}

/**
 * React key: a new revision or a new confirmation is always a new component.
 *
 * @param {LiveOrderCard} card
 */
export function orderCardKey(card) {
  const { confirmation } = card;
  return `order-card-${confirmation.provider}-${confirmation.basketId ?? "basket"}-${confirmation.basketRevision ?? "r"}-${confirmation.id}${card.superseded ? "-stale" : ""}`;
}

/**
 * The card after its tap or cancel reported a state.
 *
 * @param {LiveOrderCard | null} card
 * @param {string} confirmationId
 * @param {import("./order-confirmation.mjs").OrderCardState["status"]} result
 * @returns {LiveOrderCard | null}
 */
export function withOrderResult(card, confirmationId, result) {
  if (!card || card.confirmation.id !== confirmationId) return card;
  return { ...card, result };
}

/**
 * The store's basket now reads `basket`. An open (or failed/cancelled/expired)
 * card bound to another revision becomes an out-of-date summary.
 *
 * @param {LiveOrderCard | null} card
 * @param {BasketLike} basket
 * @returns {LiveOrderCard | null}
 */
export function supersedeOrderCard(card, basket) {
  if (!card || card.superseded || card.confirmation.provider !== basket.provider) return card;
  if (card.result && IN_FLIGHT.has(card.result)) return card;
  if (card.result === "placed") return card;
  const { basketId, basketRevision } = card.confirmation;
  if (basketId === null || basketRevision === null) return card;
  // An older read of the same basket (a late response) never supersedes a newer card.
  if (basketId === basket.id && basket.revision <= basketRevision) return card;
  return { ...card, superseded: true };
}

/**
 * The store's basket as a `basket.updated` assistant event reports it, or null
 * when the event lacks the fields a revision check needs. Untrusted input.
 *
 * @param {unknown} raw the event's `basket` object
 * @param {Store | undefined} provider the event's store
 * @returns {BasketLike | null}
 */
export function basketFromEvent(raw, provider) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const basket = /** @type {Record<string, any>} */ (raw);
  const store = basket.provider ?? provider;
  if (store !== "swiggy_instamart" && store !== "zepto") return null;
  if (provider !== undefined && store !== provider) return null;
  if (typeof basket.id !== "string" || !basket.id || !Number.isInteger(basket.revision) || basket.revision < 0) return null;
  const lines = Array.isArray(basket.lines)
    ? basket.lines.filter((line) => line && Number.isInteger(line.quantity)).map((line) => ({ quantity: line.quantity }))
    : [];
  return { id: basket.id, provider: store, revision: basket.revision, status: typeof basket.status === "string" ? basket.status : "draft", lines };
}

/**
 * True while the store may still be placing this card's order (placing, outcome
 * unknown, payment in progress): a new bill must wait until it settles.
 *
 * @param {LiveOrderCard | null | undefined} card
 */
export function orderCardInFlight(card) {
  return Boolean(card && !card.superseded && card.result && IN_FLIGHT.has(card.result));
}

/** @param {LiveOrderCard} card */
export function orderCardInteractive(card) {
  return !card.superseded && (card.result === null || card.result === "open");
}

/**
 * "Zepto order · 2 items · ₹75 · Out of date — basket changed"
 *
 * @param {LiveOrderCard} card
 */
export function supersededOrderText(card) {
  const { confirmation } = card;
  return `${confirmation.store} order · ${confirmation.itemCount} ${confirmation.itemCount === 1 ? "item" : "items"} · ${confirmation.payable} · ${STALE_ORDER_TEXT}`;
}

/**
 * Items in the header cart: open baskets only. An ordered basket is closed,
 * so the badge drops to 0 the moment the store confirms the order.
 *
 * @param {Partial<Record<Store, BasketLike | undefined>>} baskets
 */
export function cartBadgeCount(baskets) {
  return Object.values(baskets).reduce((total, basket) => total + (basket && !CLOSED_BASKET.has(basket.status) ? basket.lines.reduce((sum, line) => sum + line.quantity, 0) : 0), 0);
}

/**
 * The basket summary left behind when the store confirmed the order:
 * "Ordered · 1 item · ₹55", linked to the order's live status card.
 *
 * @param {{ itemCount: number, payablePaise: number, orderId: string }} order
 * @returns {import("./chat-timeline.mjs").BasketSummary}
 */
export function orderedSummary({ itemCount, payablePaise, orderId }) {
  return { stage: "ordered", itemCount, itemTotalPaise: payablePaise, orderId };
}
