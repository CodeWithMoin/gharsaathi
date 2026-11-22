/**
 * Rider assist (beta): pure helpers for Settings and the chat's rider-call cards.
 * The backend owns the rules (docs/RIDER_ASSIST.md); these only shape what is shown.
 */

export const RIDER_CALLS_PATH = "/api/rider/calls";
export const RIDER_SETTINGS_PATH = "/api/rider/settings";
/** The owner's app checks for new rider calls this often (in-app only, never a call). */
export const RIDER_POLL_MS = 30_000;
/** Calls older than this are not shown as chat cards. */
export const RIDER_CARD_MAX_AGE_MS = 12 * 60 * 60 * 1000;
export const RIDER_SETUP_DOC = "docs/RIDER_ASSIST.md";

export const LANDMARK_MAX = 160;
export const DIRECTIONS_MAX = 300;

export const HANDOFF_OPTIONS = [
  { value: "call_again", label: "Call me again" },
  { value: "leave_at_door", label: "Leave at the door" },
  { value: "leave_with_guard", label: "Leave with the guard" },
  { value: "hand_to_me", label: "Hand it to me only" },
];

export const NOTES_LANGUAGES = [
  { value: "hinglish", label: "Hinglish" },
  { value: "hindi", label: "Hindi" },
  { value: "english", label: "English" },
];

const SECRET = /\b(?:otp|o\.t\.p|pin|passcode|password|cvv|upi|card\s+number)\b|ओटीपी|पिन/i;
const LINK = /\bhttps?:\/\/|\bwww\./i;

/** @param {unknown} value */
const text = (value) => (typeof value === "string" ? value : "");

/**
 * @param {{ landmark?: string | null, directions?: string | null, handoff?: string, preferred_language?: string } | null | undefined} notes
 */
export function notesDraft(notes) {
  return {
    landmark: text(notes?.landmark),
    directions: text(notes?.directions),
    handoff: HANDOFF_OPTIONS.some((option) => option.value === notes?.handoff) ? /** @type {string} */ (notes?.handoff) : "call_again",
    preferred_language: NOTES_LANGUAGES.some((option) => option.value === notes?.preferred_language) ? /** @type {string} */ (notes?.preferred_language) : "hinglish",
  };
}

/**
 * Same limits as the backend; the server still validates.
 * @param {{ landmark: string, directions: string }} draft
 * @returns {string | null}
 */
export function notesError(draft) {
  const landmark = draft.landmark.trim();
  const directions = draft.directions.trim();
  if (landmark.length > LANDMARK_MAX) return `Keep the landmark under ${LANDMARK_MAX} characters.`;
  if (directions.length > DIRECTIONS_MAX) return `Keep the directions under ${DIRECTIONS_MAX} characters.`;
  if (SECRET.test(landmark) || SECRET.test(directions)) return "Don't put OTPs, PINs, passwords or payment details in delivery notes.";
  if (LINK.test(landmark) || LINK.test(directions)) return "Delivery notes can't contain links.";
  return null;
}

/** @param {{ landmark: string, directions: string, handoff: string, preferred_language: string }} draft */
export function notesPayload(draft) {
  const clean = (/** @type {string} */ value) => value.split(/\s+/).filter(Boolean).join(" ") || null;
  return { landmark: clean(draft.landmark), directions: clean(draft.directions), handoff: draft.handoff, preferred_language: draft.preferred_language };
}

/**
 * @param {{ feature_enabled: boolean, owner_configured: boolean, stream_auth_configured: boolean, enabled: boolean } | null | undefined} settings
 */
export function riderStatus(settings) {
  if (!settings) return { label: "Loading…", active: false, canToggle: false };
  if (!settings.feature_enabled) return { label: "Turned off on this server", active: false, canToggle: true };
  if (!settings.owner_configured) return { label: "This account isn't the rider-assist owner", active: false, canToggle: true };
  if (!settings.stream_auth_configured) return { label: "Server setup incomplete", active: false, canToggle: true };
  return settings.enabled ? { label: "On", active: true, canToggle: true } : { label: "Off", active: false, canToggle: true };
}

/**
 * @param {{ outcome: string, answered_from?: string[] }} call
 */
export function riderOutcomeText(call) {
  const from = new Set(call.answered_from ?? []);
  if (call.outcome === "assistant_unavailable") return "the assistant couldn't answer";
  if (call.outcome === "not_delivery") return "not a delivery call, message taken";
  if (call.outcome === "in_progress") return "the rider is on the line";
  if (from.has("notes") && from.has("order")) return "answered from your notes and order";
  if (from.has("notes")) return "answered from your notes";
  if (from.has("order")) return "answered from your order";
  if (call.outcome === "message_taken") return "message taken";
  return "call ended";
}

/**
 * "8:24 am" in India time.
 * @param {string} iso
 */
export function riderCallTime(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", hour12: true, timeZone: "Asia/Kolkata" }).replace(/\s?([ap])\.?m\.?$/i, " $1m").toLowerCase();
}

/**
 * Rider called · 8:24 am · 'Gate pe hoon, flat number?' — answered from your notes
 * @param {{ started_at: string, rider_said?: string | null, outcome: string, answered_from?: string[] }} call
 */
export function riderCallCardText(call) {
  const said = text(call.rider_said).trim();
  const quote = said ? ` · '${said.length > 80 ? `${said.slice(0, 79)}…` : said}'` : "";
  return `Rider called · ${riderCallTime(call.started_at)}${quote} — ${riderOutcomeText(call)}`;
}

/** @param {{ urgency?: string }} call */
export function riderToastText(call) {
  return call.urgency === "high" ? "A rider is waiting — see the rider call in your chat." : "A delivery partner called — GharSaathi's assistant answered.";
}

/**
 * Calls to toast now: unseen by the server and not toasted in this tab yet, oldest first.
 * @template {{ id: string, seen: boolean, started_at: string }} C
 * @param {C[]} calls
 * @param {Set<string>} toasted
 * @returns {C[]}
 */
export function newRiderCalls(calls, toasted) {
  return calls.filter((call) => !call.seen && !toasted.has(call.id)).sort((a, b) => a.started_at.localeCompare(b.started_at));
}

/**
 * Recent calls shown as chat cards (newest last).
 * @template {{ id: string, started_at: string }} C
 * @param {C[]} calls
 * @param {number} now
 * @param {Set<string>} dismissed
 * @returns {C[]}
 */
export function riderCardsToShow(calls, now, dismissed) {
  return calls
    .filter((call) => !dismissed.has(call.id) && now - new Date(call.started_at).getTime() <= RIDER_CARD_MAX_AGE_MS)
    .sort((a, b) => a.started_at.localeCompare(b.started_at));
}

/**
 * Put each rider card after the chat item it arrived after (its anchor message id), or at the end.
 * @template {{ kind: string, message?: { id: string } }} T
 * @template C
 * @param {T[]} items
 * @param {{ card: C, afterMessageId: string | null }[]} cards
 * @returns {(T | { kind: "rider-call", card: C })[]}
 */
export function placeRiderCalls(items, cards) {
  /** @type {(T | { kind: "rider-call", card: C })[]} */
  const out = [...items];
  for (const entry of cards) {
    const index = entry.afterMessageId ? out.findIndex((item) => item.kind === "message" && /** @type {any} */ (item).message?.id === entry.afterMessageId) : -1;
    let at = index < 0 ? out.length : index + 1;
    // Keep the message's own attachments (results, baskets) together with it.
    while (index >= 0 && at < out.length && out[at].kind !== "message") at += 1;
    out.splice(at, 0, { kind: "rider-call", card: entry.card });
  }
  return out;
}
