/**
 * The one-tap order card. Voice or typed chat can only *prepare* it
 * (`order.confirmation_requested`); the order is placed only when the user
 * taps "Place order" (or "Pay ₹…" for a prepaid method), which calls
 * `POST /api/orders/confirm`. Choosing another payment method asks the backend
 * for a fresh card. Prepaid orders are paid only in the user's UPI app or on
 * the store's own page; the card never asks for a PIN, card number or OTP.
 */

const PROVIDERS = new Set(["swiggy_instamart", "zepto"]);
const ID_PATTERN = /^[A-Za-z0-9_-]{8,64}$/;
/** @type {Record<string, string>} */
export const PAYMENT_METHOD_LABELS = { cod: "Cash on delivery", upi_reserve: "UPI", wallet: "Zepto Cash", online: "Pay online" };
const PAYMENT_ORDER = ["cod", "upi_reserve", "wallet", "online"];
/** Prepaid payment states a single status check can still change. */
const CHECKABLE = new Set(["awaiting_payment", "payment_succeeded", "payment_unknown"]);
const AUTO_CHECK_INTERVAL_MS = 15000;
const AUTO_CHECK_LIMIT = 20;
/** "Check with Zepto" after an unknown outcome: three automatic checks over about two minutes. */
const ORDER_CHECK_DELAYS_MS = [20_000, 45_000, 60_000];

/** @typedef {{ name: string, packSize: string | null, quantity: number }} OrderItem */
/** @typedef {"cod" | "upi_reserve" | "wallet" | "online"} PaymentMethod */
/** @typedef {{ id: PaymentMethod, label: string }} PaymentOption */
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
 *   paymentMethod: PaymentMethod,
 *   paymentMethods: PaymentOption[],
 *   basketId: string | null,
 *   basketRevision: number | null,
 * }} OrderConfirmation
 */
/** @typedef {"matched" | "not_found" | "ambiguous" | "unavailable" | "not_unknown"} OrderCheck */
/**
 * @typedef {{ status: "open" } | { status: "placing" } | { status: "placed", orderId: string, orderCode?: string | null, tracking?: unknown }
 *   | { status: "unknown", checkoutId?: string | null, check?: OrderCheck | null, candidates?: string[] }
 *   | { status: "checking_order", checkoutId: string } | { status: "failed", reason: string } | { status: "cancelling" }
 *   | { status: "cancelled" } | { status: "expired" } | { status: "switching" }
 *   | { status: "awaiting_payment", checkoutId: string, url: string | null }
 *   | { status: "checking", checkoutId: string, url: string | null }
 *   | { status: "payment_succeeded", checkoutId: string }
 *   | { status: "payment_unknown", checkoutId: string | null }
 *   | { status: "payment_failed" } | { status: "paid_order_failed" }} OrderCardState
 */

/** @param {unknown} value */
const text = (value) => (typeof value === "string" && value.trim() ? value.trim() : null);

/**
 * Known payment options in display order; Cash on delivery is always there.
 *
 * @param {unknown} raw
 * @returns {PaymentOption[]}
 */
function paymentOptions(raw) {
  const ids = new Set(["cod"]);
  if (Array.isArray(raw)) for (const option of raw) if (option && typeof option.id === "string" && option.id in PAYMENT_METHOD_LABELS) ids.add(option.id);
  return PAYMENT_ORDER.filter((id) => ids.has(id)).map((id) => ({ id: /** @type {PaymentMethod} */ (id), label: PAYMENT_METHOD_LABELS[id] }));
}

/**
 * A provider payment link the card may open: an https page or a upi:// intent.
 *
 * @param {unknown} raw
 * @returns {string | null}
 */
export function safePaymentUrl(raw) {
  const value = text(raw);
  if (!value || value.length > 2048 || /[\s\u0000-\u001f\u007f]/.test(value)) return null;
  let url;
  try { url = new URL(value); } catch { return null; }
  if (url.protocol === "https:") return url.hostname && !url.username && !url.password ? value : null;
  if (url.protocol === "upi:") return value;
  return null;
}

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
  const paymentMethods = paymentOptions(confirmation.payment_methods);
  const paymentMethod = confirmation.payment_method ?? "cod";
  if (!paymentMethods.some((option) => option.id === paymentMethod)) return null;
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
    paymentMethod,
    paymentMethods,
    basketId: text(event.basket?.id),
    basketRevision: Number.isInteger(event.basket?.revision) ? event.basket.revision : null,
  };
}

/**
 * The fresh card from `POST /api/orders/payment-method`: same bill, new id,
 * expiry and payment method. Null if the response is malformed.
 *
 * @param {OrderConfirmation} confirmation
 * @param {unknown} body
 * @returns {OrderConfirmation | null}
 */
export function withPaymentMethodCard(confirmation, body) {
  if (!body || typeof body !== "object") return null;
  const card = /** @type {Record<string, any>} */ (body);
  const id = text(card.id);
  const expiresAt = text(card.expires_at);
  if (!id || !ID_PATTERN.test(id) || !expiresAt || Number.isNaN(Date.parse(expiresAt))) return null;
  const paymentMethods = paymentOptions(card.payment_methods);
  if (!paymentMethods.some((option) => option.id === card.payment_method)) return null;
  return { ...confirmation, id, expiresAt, paymentMethod: card.payment_method, paymentMethods };
}

/** @param {OrderConfirmation} confirmation */
export function placeButtonLabel(confirmation) {
  return confirmation.paymentMethod === "cod" ? `Place order · ${confirmation.payable}` : `Pay ${confirmation.payable}`;
}

/** @param {OrderConfirmation} confirmation */
export function paymentMethodLabel(confirmation) {
  return PAYMENT_METHOD_LABELS[confirmation.paymentMethod] ?? "Cash on delivery";
}

/**
 * The checkout id a single "check status" tap may use, or null.
 *
 * @param {OrderCardState} state
 * @returns {string | null}
 */
export function checkableCheckoutId(state) {
  if (!CHECKABLE.has(state.status)) return null;
  return "checkoutId" in state && state.checkoutId ? state.checkoutId : null;
}

/**
 * Delay before the card's next automatic single status check, or null to stop.
 * The backend also rate-limits checks per order.
 *
 * @param {number} checksSoFar
 */
export function autoCheckDelayMs(checksSoFar) {
  return checksSoFar < AUTO_CHECK_LIMIT ? AUTO_CHECK_INTERVAL_MS : null;
}

/**
 * The checkout id a "Check with Zepto" tap may use (unknown outcome only), or null.
 *
 * @param {OrderCardState} state
 * @returns {string | null}
 */
export function checkableOrderId(state) {
  return state.status === "unknown" && state.checkoutId ? state.checkoutId : null;
}

/**
 * Delay before the next automatic read-only order check, or null to stop.
 * The backend runs the same bounded checks and rate-limits them.
 *
 * @param {number} checksSoFar
 */
export function orderCheckDelayMs(checksSoFar) {
  return ORDER_CHECK_DELAYS_MS[checksSoFar] ?? null;
}

/** @param {unknown} raw @returns {OrderCheck | null} */
function orderCheck(raw) {
  return raw === "matched" || raw === "not_found" || raw === "ambiguous" || raw === "unavailable" || raw === "not_unknown" ? raw : null;
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
    case "placed": return `Order placed · #${state.orderCode ?? state.orderId}`;
    case "checking_order": return `Checking ${store}'s order history…`;
    case "unknown":
      if (state.check === "ambiguous") return `Several ${store} orders could match${state.candidates?.length ? ` (${state.candidates.slice(0, 3).map((code) => `#${code}`).join(", ")})` : ""}. GharSaathi won't guess. Check the ${store} app; don't order again.`;
      if (state.check === "not_found") return `${store} hasn't confirmed this order. Its order history doesn't show it yet. Check the ${store} app before trying again.`;
      return `${store} hasn't confirmed this order yet. Don't order again; check with ${store} first.`;
    case "failed": return `Order not placed: ${state.reason}`;
    case "cancelled": return "Cancelled. Nothing was ordered.";
    case "expired": return "Expired. Nothing was ordered. Ask for the order again.";
    case "awaiting_payment":
    case "checking": return `Waiting for your payment. Finish it in your UPI app or on ${store}'s payment page, then check the status. The order isn't placed until ${store} confirms it.`;
    case "payment_succeeded": return `${store} confirmed the payment but not the order yet. Don't pay again. Check the status again in a moment.`;
    case "payment_unknown": return `${store} hasn't confirmed the payment. Don't pay again. Check the status here or in your UPI app.`;
    case "payment_failed": return `The payment didn't go through, so the order wasn't placed. Check your UPI app and the ${store} app before trying again.`;
    case "paid_order_failed": return `${store} says the payment went through but the order wasn't created. Don't pay again. Contact ${store} support in the ${store} app.`;
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
    const body = outcome.body;
    const orderId = text(body?.provider_order_id);
    const checkoutId = text(body?.checkout_id);
    switch (body?.status) {
      // `tracking` is the read-only status monitor the backend started for this order, if any.
      case "confirmed": return orderId ? { status: "placed", orderId, orderCode: text(body?.provider_order_code), tracking: body?.tracking ?? null } : { status: "unknown", checkoutId };
      case "awaiting_payment": return checkoutId ? { status: "awaiting_payment", checkoutId, url: safePaymentUrl(body?.payment?.url) } : { status: "payment_unknown", checkoutId: null };
      case "payment_succeeded": return checkoutId ? { status: "payment_succeeded", checkoutId } : { status: "payment_unknown", checkoutId: null };
      case "payment_unknown": return { status: "payment_unknown", checkoutId };
      case "payment_failed": return { status: "payment_failed" };
      case "paid_order_failed": return { status: "paid_order_failed" };
      default: return {
        status: "unknown",
        checkoutId,
        check: orderCheck(body?.check),
        candidates: Array.isArray(body?.candidates) ? body.candidates.map(text).filter((code) => code !== null).slice(0, 5) : [],
      };
    }
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
