/**
 * Calls to the Sarvam inbound delivery line: pure helpers for the chat card and the
 * order status line. The backend stores a redacted summary per call
 * (backend/app/inbound_calls.py); these only shape what is shown. Copy matches the
 * backend's browser notification wording.
 */

import { riderCallTime } from "./rider-assist.mjs";

export const INBOUND_CALLS_PATH = "/api/inbound-delivery/calls";
export const INBOUND_CONFIG_PATH = "/api/inbound-delivery/config";
/** The app re-reads stored calls this often while visible (no Sarvam request from the browser). */
export const INBOUND_POLL_MS = 30_000;
/** Calls older than this are not shown as chat cards. */
export const INBOUND_CARD_MAX_AGE_MS = 12 * 60 * 60 * 1000;

export const CALL_OUTCOMES = [
  "route_confirmed",
  "owner_transfer_requested",
  "provider_contact_needed",
  "unauthorized_caller",
  "general_request_handled",
  "out_of_scope",
  "no_decision",
];

const STORES = { zepto: "Zepto", swiggy: "Swiggy" };

/** @param {string | null | undefined} provider */
export function inboundStoreName(provider) {
  return provider && Object.prototype.hasOwnProperty.call(STORES, provider) ? STORES[/** @type {"zepto" | "swiggy"} */ (provider)] : null;
}

/**
 * @param {string} outcome
 * @param {string | null | undefined} provider
 */
export function inboundOutcomeText(outcome, provider) {
  const store = inboundStoreName(provider);
  switch (outcome) {
    case "route_confirmed": return "Route confirmed";
    case "owner_transfer_requested": return "Caller asked for you";
    case "provider_contact_needed": return `Rider needed help — check the ${store ?? "store"} app`;
    case "unauthorized_caller": return "Unverified caller — no address shared";
    case "general_request_handled": return "Question answered";
    case "out_of_scope": return "Not about a delivery";
    default: return "No outcome recorded";
  }
}

/**
 * @param {string | null | undefined} provider
 * @param {string} outcome
 */
export function inboundHeadline(provider, outcome) {
  if (outcome === "unauthorized_caller" || outcome === "out_of_scope") return "Someone called the delivery line";
  const store = inboundStoreName(provider);
  return store ? `${store} rider called` : "A delivery partner called";
}

/** `2:14 pm` in India time (same format as rider-assist cards). */
export const inboundCallTime = riderCallTime;

/**
 * "Zepto rider called · 2:14 pm · Route confirmed — <summary>".
 * @param {{ started_at: string, provider?: string | null, outcome: string, summary?: string | null }} call
 */
export function inboundCallCardText(call) {
  const parts = [inboundHeadline(call.provider, call.outcome), inboundCallTime(call.started_at), inboundOutcomeText(call.outcome, call.provider)].filter(Boolean);
  const line = parts.join(" · ");
  return call.summary ? `${line} — ${call.summary}` : line;
}

/** @param {{ outcome: string }} call */
export function inboundCallUrgent(call) {
  return call.outcome === "owner_transfer_requested" || call.outcome === "provider_contact_needed";
}

/**
 * Recent calls not dismissed, oldest first (they read top to bottom).
 * @template {{ id: string, started_at: string }} C
 * @param {C[]} calls
 * @param {number} now
 * @param {Set<string>} dismissed
 * @returns {C[]}
 */
export function inboundCardsToShow(calls, now, dismissed) {
  return calls
    .filter((call) => !dismissed.has(call.id))
    .filter((call) => {
      const at = Date.parse(call.started_at);
      return Number.isFinite(at) && now - at <= INBOUND_CARD_MAX_AGE_MS;
    })
    .sort((a, b) => Date.parse(a.started_at) - Date.parse(b.started_at));
}

/**
 * The order status card's line: "Rider has your directions" once a route was confirmed
 * on a call for this store after the order was placed.
 * @param {{ started_at: string, provider?: string | null, outcome: string }[]} calls
 * @param {{ store: string, placedAt?: string | null, status?: string }} order
 * @returns {string | null}
 */
export function riderDirectionsLine(calls, order) {
  if (order.status === "delivered" || order.status === "cancelled") return null;
  const placed = order.placedAt ? Date.parse(order.placedAt) : Number.NaN;
  const confirmed = calls.some((call) => call.outcome === "route_confirmed"
    && (!call.provider || inboundStoreName(call.provider) === order.store)
    && (!Number.isFinite(placed) || Date.parse(call.started_at) >= placed));
  return confirmed ? "Rider has your directions" : null;
}

/**
 * Put the call cards right after the order status card (else at the end).
 * @template {{ kind: string }} T
 * @template C
 * @param {T[]} items
 * @param {C[]} cards
 * @returns {(T | { kind: "inbound-call", card: C })[]}
 */
export function placeInboundCalls(items, cards) {
  if (!cards.length) return items;
  let at = items.length;
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (items[index].kind === "order-status") { at = index + 1; break; }
  }
  /** @type {(T | { kind: "inbound-call", card: C })[]} */
  const out = [...items];
  out.splice(at, 0, ...cards.map((card) => ({ kind: /** @type {const} */ ("inbound-call"), card })));
  return out;
}
