/**
 * The live status card a placed order becomes in the chat timeline.
 *
 * The card only shows what the backend tracking monitor reports (read-only
 * provider status). It never guesses a step or a time: an unknown provider
 * status is shown as the store's own words, and the ETA is the store's own
 * estimate when it gave one.
 */

export const ORDER_STEPS = /** @type {const} */ (["confirmed", "preparing", "out_for_delivery", "delivered"]);
export const CARD_STATUSES = /** @type {const} */ (["unknown", "confirmed", "preparing", "out_for_delivery", "delivered", "cancelled", "paused", "unavailable"]);
export const MAX_ORDER_CARDS = 10;
export const MIN_POLL_MS = 5_000;
export const MAX_POLL_MS = 5 * 60_000;

const STEP_LABELS = { confirmed: "Placed", preparing: "Packed", out_for_delivery: "Out for delivery", delivered: "Delivered" };
const HEADLINES = {
  unknown: "Order placed",
  confirmed: "Order placed",
  preparing: "Being packed",
  out_for_delivery: "Out for delivery",
  delivered: "Delivered",
  cancelled: "Cancelled",
  paused: "Live status paused",
  unavailable: "Live status unavailable",
};

/** @typedef {typeof CARD_STATUSES[number]} CardStatus */
/**
 * @typedef {{
 *   provider: "zepto",
 *   orderId: string,
 *   monitorId: string | null,
 *   store: string,
 *   afterMessageId: string | null,
 *   seq: number,
 *   itemCount: number | null,
 *   payable: string | null,
 *   status: CardStatus,
 *   providerStatus: string | null,
 *   etaAt: string | null,
 *   checkedAt: string | null,
 *   active: boolean,
 *   lastNotificationId: string | null,
 *   itemNames: string[],
 *   finishedAt: string | null,
 *   placedAt: string | null,
 *   tookMinutes: number | null,
 *   estimateMinutes: number | null,
 * }} OrderStatusCard
 */
/** @typedef {{ id: string, label: string, state: "done" | "current" | "todo" | "cancelled" }} OrderStep */

/** @param {unknown} value */
const text = (value) => (typeof value === "string" && value.trim() ? value.trim() : null);
/** @param {unknown} value */
const time = (value) => {
  const value_ = text(value);
  return value_ && !Number.isNaN(Date.parse(value_)) ? value_ : null;
};
/** @param {unknown} value */
const minutes = (value) => (typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null);
/** @param {unknown} value @returns {CardStatus} */
const cardStatus = (value) => CARD_STATUSES.find((status) => status === value) ?? "unknown";

/**
 * The four steps, with how far this order has got.
 *
 * @param {CardStatus} status
 * @returns {OrderStep[]}
 */
export function orderSteps(status) {
  const reached = status === "delivered" ? 3 : status === "out_for_delivery" ? 2 : status === "preparing" ? 1 : 0;
  return ORDER_STEPS.map((id, index) => {
    if (status === "cancelled" && index === ORDER_STEPS.length - 1) return { id: "cancelled", label: "Cancelled", state: "cancelled" };
    if (status === "cancelled") return { id, label: STEP_LABELS[id], state: index === 0 ? "done" : "todo" };
    const known = status === "confirmed" || status === "preparing" || status === "out_for_delivery" || status === "delivered";
    if (!known) return { id, label: STEP_LABELS[id], state: index === 0 ? "done" : "todo" };
    const state = index < reached || status === "delivered" ? "done" : index === reached ? "current" : "todo";
    return { id, label: STEP_LABELS[id], state };
  });
}

/**
 * The card's headline: our step name, or the store's own words for a status we don't map.
 *
 * @param {Pick<OrderStatusCard, "status" | "providerStatus">} card
 */
export function orderHeadline(card) {
  if (card.status === "unknown" && card.providerStatus) return card.providerStatus;
  return HEADLINES[card.status];
}

/** @param {OrderStatusCard} card */
export function isFinal(card) {
  return card.status === "delivered" || card.status === "cancelled";
}

/**
 * A new card for a provider-confirmed order (`POST /api/orders/confirm`).
 *
 * @param {{ orderId: string, store: string, afterMessageId: string | null, seq: number, itemCount?: number | null, payable?: string | null, itemNames?: string[], tracking?: unknown }} input
 * @returns {OrderStatusCard}
 */
export function orderCardFromPlaced({ orderId, store, afterMessageId, seq, itemCount = null, payable = null, itemNames = [], tracking = null }) {
  /** @type {OrderStatusCard} */
  const card = { provider: "zepto", orderId, monitorId: null, store, afterMessageId, seq, itemCount, payable, status: "confirmed", providerStatus: null, etaAt: null, checkedAt: null, active: true, lastNotificationId: null, itemNames: names(itemNames), finishedAt: null, placedAt: null, tookMinutes: null, estimateMinutes: null };
  if (!tracking) return { ...card, active: false };
  const applied = applyTrackingRead(card, tracking);
  // Notifications that existed before the card was shown are not news.
  return { ...applied.card, lastNotificationId: latestNotificationId(tracking) };
}

/** @param {unknown} read */
function latestNotificationId(read) {
  const list = /** @type {any} */ (read)?.order?.notifications;
  return Array.isArray(list) && list.length ? text(list[list.length - 1]?.id) : null;
}

/**
 * Merges one `TrackingRead` into the card. `changed` is true only when
 * something worth saving with the chat changed (the check time alone is not);
 * `toast` is the newest in-app update the user has not seen yet.
 *
 * @param {OrderStatusCard} card
 * @param {unknown} raw
 * @returns {{ card: OrderStatusCard, changed: boolean, toast: string | null }}
 */
export function applyTrackingRead(card, raw) {
  const read = /** @type {any} */ (raw);
  const order = read?.order;
  if (!order || typeof order !== "object" || text(order.order_id) !== card.orderId) return { card, changed: false, toast: null };
  const snapshot = order.snapshot && typeof order.snapshot === "object" ? order.snapshot : null;
  let status = cardStatus(order.status);
  // Tracking stopped on a provider error: keep showing the last store-reported step.
  if ((status === "paused" || status === "unavailable") && snapshot) {
    const last = snapshot.delivered === true ? "delivered" : snapshot.cancelled === true ? "cancelled" : cardStatus(snapshot.status);
    if (last !== "unknown") status = last;
  }
  const final = status === "delivered" || status === "cancelled";
  // Zepto's own delivered time wins over when we happened to check.
  const deliveredAt = status === "delivered" ? time(snapshot?.delivered_at) : null;
  const next = {
    ...card,
    monitorId: text(read.monitor_id) ?? text(order.id) ?? card.monitorId,
    status,
    providerStatus: text(snapshot?.provider_status) ?? card.providerStatus,
    etaAt: final ? null : time(snapshot?.eta_at),
    checkedAt: time(order.status_at) ?? card.checkedAt,
    active: order.active === true,
    finishedAt: final ? deliveredAt ?? card.finishedAt ?? time(order.status_at) ?? time(order.checked_at) : null,
    placedAt: time(snapshot?.placed_at) ?? card.placedAt ?? null,
    tookMinutes: status === "delivered" ? minutes(snapshot?.took_minutes) ?? card.tookMinutes ?? null : null,
    estimateMinutes: minutes(snapshot?.committed_eta_minutes) ?? card.estimateMinutes ?? null,
  };
  const notifications = Array.isArray(order.notifications) ? order.notifications.filter((item) => text(item?.id) && text(item?.message)) : [];
  const seenIndex = card.lastNotificationId ? notifications.findIndex((item) => item.id === card.lastNotificationId) : -1;
  const unseen = notifications.slice(seenIndex + 1);
  const toast = unseen.length ? text(unseen[unseen.length - 1].message) : null;
  if (unseen.length) next.lastNotificationId = unseen[unseen.length - 1].id;
  const changed = ["monitorId", "status", "providerStatus", "etaAt", "active", "lastNotificationId", "finishedAt", "placedAt", "tookMinutes", "estimateMinutes"].some((key) => next[/** @type {keyof OrderStatusCard} */ (key)] !== card[/** @type {keyof OrderStatusCard} */ (key)]);
  return { card: changed || next.checkedAt !== card.checkedAt ? next : card, changed, toast };
}

/**
 * When to ask the backend again: at its next allowed provider check, within
 * bounds, or never once tracking has stopped.
 *
 * @param {unknown} raw
 * @param {number} nowMs
 * @returns {number | null}
 */
export function pollDelayMs(raw, nowMs) {
  const read = /** @type {any} */ (raw);
  if (!read?.order || read.order.active !== true) return null;
  const next = Date.parse(read.next_check_at ?? "");
  if (Number.isNaN(next)) return 60_000;
  return Math.min(MAX_POLL_MS, Math.max(MIN_POLL_MS, next - nowMs + 1_000));
}

/**
 * "Arriving by 8:26 am · about 4 min" from the store's estimated time, or
 * "Running late — Zepto estimated 8:26 am" once that time has passed.
 *
 * @param {Pick<OrderStatusCard, "etaAt" | "status" | "store">} card
 * @param {number} nowMs
 * @returns {string | null}
 */
export function etaText(card, nowMs) {
  if (!card.etaAt || card.status === "delivered" || card.status === "cancelled") return null;
  const at = Date.parse(card.etaAt);
  if (Number.isNaN(at)) return null;
  const left = Math.ceil((at - nowMs) / 60_000);
  if (left > 0) return `Arriving by ${clock(at)} · about ${left} min`;
  return `Running late — ${card.store} estimated ${clock(at)}`;
}

/**
 * "Placed at 8:18 am", from the store's placed time.
 *
 * @param {Pick<OrderStatusCard, "placedAt">} card
 * @returns {string | null}
 */
export function placedText(card) {
  const at = card.placedAt ? Date.parse(card.placedAt) : NaN;
  return Number.isNaN(at) ? null : `Placed at ${clock(at)}`;
}

/**
 * "8:26 am · took 9 min (estimate 8 min)" for a delivered order; parts the
 * store did not report are left out.
 *
 * @param {Pick<OrderStatusCard, "status" | "finishedAt" | "tookMinutes" | "estimateMinutes">} card
 * @returns {string | null}
 */
export function deliveredText(card) {
  if (card.status !== "delivered") return null;
  const at = card.finishedAt ? Date.parse(card.finishedAt) : NaN;
  const took = card.tookMinutes !== null && card.tookMinutes !== undefined
    ? `took ${card.tookMinutes} min${card.estimateMinutes ? ` (estimate ${card.estimateMinutes} min)` : ""}`
    : null;
  const parts = [Number.isNaN(at) ? null : clock(at), took].filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}

/** @param {number} ms */
function clock(ms) {
  return new Intl.DateTimeFormat("en-IN", { hour: "numeric", minute: "2-digit" }).format(new Date(ms));
}

/**
 * "Updated just now" / "Updated 4 min ago" / "Updated at 6:12 pm".
 *
 * @param {string | null} checkedAt
 * @param {number} nowMs
 */
export function updatedText(checkedAt, nowMs) {
  if (!checkedAt) return null;
  const at = Date.parse(checkedAt);
  if (Number.isNaN(at)) return null;
  const minutes = Math.floor((nowMs - at) / 60_000);
  if (minutes < 1) return "Updated just now";
  if (minutes < 60) return `Updated ${minutes} min ago`;
  return `Updated at ${clock(at)}`;
}

/** @param {unknown} raw @returns {string[]} */
function names(raw) {
  return Array.isArray(raw) ? raw.map(text).filter((name) => name !== null).map((name) => name.slice(0, 120)).slice(0, 5) : [];
}

/**
 * The one-line summary a delivered or store-cancelled order collapses to:
 * "Delivered ✓ · 8:26 am · took 9 min (estimate 8 min)" /
 * "Cancelled by Zepto · Amul Sweet Lassi · ₹55 · 8:41 am". Items and total of a
 * delivered order are in the expanded card. Null while the order is still moving.
 *
 * @param {OrderStatusCard} card
 * @returns {string | null}
 */
export function finishedOrderSummary(card) {
  if (!isFinal(card)) return null;
  if (card.status === "delivered") return ["Delivered ✓", deliveredText(card)].filter(Boolean).join(" · ");
  const head = `Cancelled by ${card.store}`;
  const [first, ...rest] = card.itemNames ?? [];
  const items = first ? `${first}${rest.length ? ` +${rest.length} more` : ""}` : card.itemCount !== null ? `${card.itemCount} ${card.itemCount === 1 ? "item" : "items"}` : null;
  const at = card.finishedAt ? Date.parse(card.finishedAt) : NaN;
  return [head, items, card.payable, Number.isNaN(at) ? null : clock(at)].filter(Boolean).join(" · ");
}

/** @param {OrderStatusCard} card */
export function orderCardSnapshot(card) {
  return {
    provider: card.provider,
    order_id: card.orderId,
    monitor_id: card.monitorId,
    store: card.store,
    after_message_id: card.afterMessageId,
    seq: card.seq,
    item_count: card.itemCount,
    payable: card.payable,
    status: card.status,
    provider_status: card.providerStatus,
    eta_at: card.etaAt,
    checked_at: card.checkedAt,
    active: card.active,
    last_notification_id: card.lastNotificationId,
    item_names: card.itemNames ?? [],
    finished_at: card.finishedAt ?? null,
    placed_at: card.placedAt ?? null,
    took_minutes: card.tookMinutes ?? null,
    estimate_minutes: card.estimateMinutes ?? null,
  };
}

/**
 * Reads one saved card; malformed entries are dropped.
 *
 * @param {unknown} raw
 * @returns {OrderStatusCard | null}
 */
export function restoreOrderCard(raw) {
  if (!raw || typeof raw !== "object") return null;
  const value = /** @type {Record<string, unknown>} */ (raw);
  const orderId = text(value.order_id);
  const seq = typeof value.seq === "number" && Number.isInteger(value.seq) && value.seq >= 0 ? value.seq : null;
  if (value.provider !== "zepto" || !orderId || seq === null) return null;
  const count = typeof value.item_count === "number" && Number.isInteger(value.item_count) && value.item_count >= 0 ? value.item_count : null;
  return {
    provider: "zepto",
    orderId,
    monitorId: text(value.monitor_id),
    store: text(value.store) ?? "Zepto",
    afterMessageId: text(value.after_message_id),
    seq,
    itemCount: count,
    payable: text(value.payable),
    status: cardStatus(value.status),
    providerStatus: text(value.provider_status),
    etaAt: time(value.eta_at),
    checkedAt: time(value.checked_at),
    active: value.active !== false,
    lastNotificationId: text(value.last_notification_id),
    itemNames: names(value.item_names),
    finishedAt: time(value.finished_at),
    placedAt: time(value.placed_at),
    tookMinutes: minutes(value.took_minutes),
    estimateMinutes: minutes(value.estimate_minutes),
  };
}
