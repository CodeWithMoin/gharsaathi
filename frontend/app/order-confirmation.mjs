/**
 * The one-tap order card. Voice or typed chat can only *prepare* it
 * (`order.confirmation_requested`); the order is placed only when the user
 * taps "Place order", which calls `POST /api/orders/confirm`.
 */

const PROVIDERS = new Set(["swiggy_instamart", "zepto"]);
const ID_PATTERN = /^[A-Za-z0-9_-]{8,64}$/;

/** @typedef {{ name: string, packSize: string | null, quantity: number }} OrderItem */
/**
 * @typedef {{
 *   id: string,
 *   provider: "swiggy_instamart" | "zepto",
 *   store: string,
 *   expiresAt: string,
 *   payable: string,
 *   payablePaise: number,
 *   itemCount: number,
 *   items: OrderItem[],
 *   addressLabel: string | null,
 *   address: string | null,
 * }} OrderConfirmation
 */
/**
 * @typedef {{ status: "open" } | { status: "placing" } | { status: "placed", orderId: string }
 *   | { status: "unknown" } | { status: "failed", reason: string } | { status: "cancelling" }
 *   | { status: "cancelled" } | { status: "expired" }} OrderCardState
 */

/** @param {unknown} value */
const text = (value) => (typeof value === "string" && value.trim() ? value.trim() : null);

/**
 * Validates an untrusted `order.confirmation_requested` event.
 *
 * @param {unknown} raw
 * @returns {OrderConfirmation | null}
 */
export function orderConfirmationFromEvent(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const event = /** @type {Record<string, any>} */ (raw);
  if (event.type !== "order.confirmation_requested" || !PROVIDERS.has(event.provider)) return null;
  const confirmation = event.confirmation;
  if (!confirmation || typeof confirmation !== "object") return null;
  const id = text(confirmation.id);
  const expiresAt = text(confirmation.expires_at);
  const payable = text(confirmation.payable);
  if (!id || !ID_PATTERN.test(id) || !expiresAt || Number.isNaN(Date.parse(expiresAt)) || !payable) return null;
  if (!Number.isInteger(confirmation.payable_paise) || confirmation.payable_paise < 0) return null;
  if (!Array.isArray(confirmation.items) || !confirmation.items.length) return null;
  /** @type {OrderItem[]} */
  const items = [];
  for (const item of confirmation.items) {
    const name = text(item?.name);
    if (!name || !Number.isInteger(item.quantity) || item.quantity < 1) return null;
    items.push({ name, packSize: text(item.pack_size), quantity: item.quantity });
  }
  const address = confirmation.delivery_address && typeof confirmation.delivery_address === "object" ? confirmation.delivery_address : {};
  return {
    id,
    provider: event.provider,
    store: text(event.store) ?? (event.provider === "zepto" ? "Zepto" : "Swiggy Instamart"),
    expiresAt,
    payable,
    payablePaise: confirmation.payable_paise,
    itemCount: Number.isInteger(confirmation.item_count) ? confirmation.item_count : items.reduce((total, item) => total + item.quantity, 0),
    items,
    addressLabel: text(address.label),
    address: text(address.address),
  };
}

/**
 * "Expires in 4:59", or null once expired.
 *
 * @param {string} expiresAt
 * @param {number} nowMs
 */
export function countdownLabel(expiresAt, nowMs) {
  const left = Math.floor((Date.parse(expiresAt) - nowMs) / 1000);
  if (!(left > 0)) return null;
  return `Expires in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
}

/** @param {OrderConfirmation} confirmation */
export function deliverToLabel(confirmation) {
  const where = confirmation.addressLabel ?? confirmation.address;
  return where ? `Deliver to: ${where}` : null;
}

/**
 * The result line shown on the card once it is no longer open.
 *
 * @param {OrderCardState} state
 * @param {string} store
 * @returns {string | null}
 */
export function orderCardResultText(state, store) {
  switch (state.status) {
    case "placed": return `Order placed · #${state.orderId}`;
    case "unknown": return `${store} hasn't confirmed this order yet. Check order tracking before trying again.`;
    case "failed": return `Order not placed: ${state.reason}`;
    case "cancelled": return "Cancelled. Nothing was ordered.";
    case "expired": return "Expired. Nothing was ordered. Ask for the order again.";
    default: return null;
  }
}

/**
 * Maps the `POST /api/orders/confirm` outcome to the card state. Only a
 * provider-confirmed order id counts as placed.
 *
 * @param {{ ok: true, body: any } | { ok: false, status: number, message: string }} outcome
 * @returns {OrderCardState}
 */
export function orderCardStateFromResponse(outcome) {
  if (outcome.ok) {
    const orderId = text(outcome.body?.provider_order_id);
    if (outcome.body?.status === "confirmed" && orderId) return { status: "placed", orderId };
    return { status: "unknown" };
  }
  // A dropped connection or server error is an uncertain outcome, never a reason to tap again.
  if (outcome.status === 0 || outcome.status >= 500 && outcome.status !== 502 && outcome.status !== 503) return { status: "unknown" };
  return { status: "failed", reason: outcome.message || "The store did not accept the order." };
}

/**
 * Where the card goes in the laid-out timeline: after its anchor message and
 * the items already attached to it, or at the end when the anchor is gone.
 *
 * @param {{ kind: string, message?: { id: string } }[]} items
 * @param {string | null} anchorId
 */
export function orderCardIndex(items, anchorId) {
  const start = anchorId === null ? -1 : items.findIndex((item) => item.kind === "message" && item.message?.id === anchorId);
  if (start === -1) return items.length;
  let index = start + 1;
  while (index < items.length && items[index].kind !== "message") index += 1;
  return index;
}
