// Pure logic for the "Delivery line" panel in order tracking: the directions draft
// and which state the panel shows. The server decides readiness; this only reads it.

export const DIRECTIONS_PATH = "/api/inbound-delivery/directions";
export const LINE_PATH = "/api/inbound-delivery/line";
export const LANDMARK_MAX = 120;
export const STEP_MAX = 80;
export const MAX_STEPS = 8;
export const GATE_NOTE_MAX = 120;
export const HANDOVER_MAX = 100;

export const HANDOVER_CHIPS = [
  "Guard ke paas chhod dijiye",
  "Door ke bahar rakh dijiye",
  "Neeche aake call kijiye — main aa jaunga",
  "Mujhe hi dijiye",
];

const ONE_LINE = /[\u0000-\u0008\u000a-\u001f\u007f]/;
const SECRET = /\b(?:otp|o\.t\.p|pin|passcode|password|cvv|upi|card\s+number)\b|ओटीपी|पिन/i;
const LINK = /\bhttps?:\/\/|\bwww\./i;

const tidy = (value) => String(value ?? "").replace(/\s+/g, " ").trim();

export function emptyDirectionsDraft() {
  return { landmark: "", steps: [""], gate_note: "", handover_instructions: "" };
}

export function directionsDraft(saved) {
  if (!saved) return emptyDirectionsDraft();
  const steps = Array.isArray(saved.route_directions) ? saved.route_directions.filter((step) => typeof step === "string") : [];
  return {
    landmark: saved.landmark ?? "",
    steps: steps.length ? steps.slice(0, MAX_STEPS) : [""],
    gate_note: saved.gate_note ?? "",
    handover_instructions: saved.handover_instructions ?? "",
  };
}

export function addStep(draft) {
  if (draft.steps.length >= MAX_STEPS) return draft;
  return { ...draft, steps: [...draft.steps, ""] };
}

export function removeStep(draft, index) {
  const steps = draft.steps.filter((_, position) => position !== index);
  return { ...draft, steps: steps.length ? steps : [""] };
}

export function updateStep(draft, index, value) {
  return { ...draft, steps: draft.steps.map((step, position) => (position === index ? value : step)) };
}

/** Tapping the selected chip again clears it; otherwise the chip replaces the text. */
export function pickHandover(draft, chip) {
  return { ...draft, handover_instructions: tidy(draft.handover_instructions) === chip ? "" : chip };
}

export function directionsError(draft) {
  const landmark = tidy(draft.landmark);
  const steps = draft.steps.map(tidy).filter(Boolean);
  const fields = [draft.landmark, ...draft.steps, draft.gate_note, draft.handover_instructions];
  if (landmark.length < 3) return "Add a landmark the delivery partner can spot (at least 3 characters).";
  if (landmark.length > LANDMARK_MAX) return `Keep the landmark under ${LANDMARK_MAX} characters.`;
  if (steps.length > MAX_STEPS) return `Use at most ${MAX_STEPS} steps.`;
  if (steps.some((step) => step.length > STEP_MAX)) return `Keep each step under ${STEP_MAX} characters.`;
  if (tidy(draft.gate_note).length > GATE_NOTE_MAX) return `Keep the gate note under ${GATE_NOTE_MAX} characters.`;
  if (tidy(draft.handover_instructions).length > HANDOVER_MAX) return `Keep the handover note under ${HANDOVER_MAX} characters.`;
  if (fields.some((value) => ONE_LINE.test(String(value ?? "")))) return "Keep each field on one line.";
  if (fields.some((value) => SECRET.test(String(value ?? "")))) return "Don't add OTPs, PINs, passwords or payment details.";
  if (fields.some((value) => LINK.test(String(value ?? "")))) return "Don't add links.";
  return null;
}

export function directionsPayload(draft, provider, addressId) {
  return {
    provider,
    address_id: addressId,
    landmark: tidy(draft.landmark),
    route_directions: draft.steps.map(tidy).filter(Boolean).slice(0, MAX_STEPS),
    gate_note: tidy(draft.gate_note) || null,
    handover_instructions: tidy(draft.handover_instructions) || null,
  };
}

export function directionsDirty(draft, initial) {
  const comparable = (value) => JSON.stringify({ ...directionsPayload(value, "", ""), provider: undefined, address_id: undefined });
  return comparable(draft) !== comparable(initial);
}

const REASON_TEXT = {
  address_changed: "You changed the delivery address.",
  delivered: "The order was delivered.",
  cancelled: "The order was cancelled.",
  user_cleared: "",
};

/**
 * What the panel shows. Inputs come from GET /config, /line and /directions plus
 * the app's selected address and profile name.
 */
export function deliveryLineView({ config, address, recipientName, directions, line, store }) {
  if (!config) return { kind: "loading" };
  if (!config.enabled) {
    const message = config.reason === "not_owner"
      ? "Only the account owner can use the delivery line."
      : config.reason === "owner_not_configured"
        ? "The delivery line has no owner set on this server."
        : "The delivery line is not set up on this server.";
    return { kind: "off", message };
  }
  const sameAddress = Boolean(line && address && line.provider && line.address_id === address.id);
  const phase = line?.phase ?? "none";
  const callHint = `The approved caller ${config.approved_caller_hint ?? ""} can call ${config.inbound_phone_number ?? "the delivery line"} and say “Sir, ${store}.”`.replace(/\s+/g, " ");
  if (phase === "clearing") return { kind: "clearing", tone: "warning", message: "Clearing the delivery line…" };
  if (phase === "cleared") return { kind: "cleared", tone: "neutral", message: `Delivery line cleared. ${REASON_TEXT[line.reason] ?? ""}`.trim(), canShare: canShare() };
  if (phase === "clear_failed") return { kind: "clear_failed", tone: "danger", message: "Couldn't confirm the delivery line was cleared. Clear it again.", canShare: false };
  if (sameAddress && phase === "ready") return { kind: "ready", tone: "success", message: `Ready. ${callHint}` };
  if (sameAddress && phase === "processing") return { kind: "processing", tone: "warning", message: "Sharing with the delivery line…" };
  if (sameAddress && phase === "unknown") return { kind: "unknown", tone: "danger", message: "It isn't known whether the delivery line got your details. Clear it, then share again.", canShare: false };
  if (sameAddress && phase === "rejected") return { kind: "rejected", tone: "danger", message: "Sarvam rejected the details. Don't rely on the delivery line.", canShare: canShare() };
  if (sameAddress && phase === "failed") return { kind: "failed", tone: "danger", message: "Sarvam couldn't process the details. Don't rely on the delivery line.", canShare: canShare() };
  return { kind: "idle", tone: "neutral", message: needs() ?? "", canShare: canShare() };

  function needs() {
    if (!address) return "Choose a delivery address first.";
    if (tidy(recipientName).length < 2) return "Add the recipient's name first.";
    if (!directions?.landmark) return "Add directions for this address first.";
    return null;
  }

  function canShare() {
    return needs() === null;
  }
}

export function stepsSummary(directions) {
  const steps = directions?.route_directions ?? [];
  return steps.length ? steps.map((step, index) => `${index + 1}. ${step}`) : [];
}

export const LIVE_PREVIEW_PATH = "/api/inbound-delivery/live-preview";
const LIVE_STORES = { zepto: "Zepto", swiggy: "Swiggy" };
const known = (value) => typeof value === "string" && value.trim() && value !== "NOT_AVAILABLE" ? value.trim() : null;

/**
 * "Live rider help": what the Sarvam agent's get_delivery_context tool would answer
 * a rider calling right now (GET /live-preview). Null hides the section (off, or
 * not readable). The server decides everything; this only words it.
 */
export function liveRiderHelpView(preview) {
  if (!preview || preview.enabled !== true || !preview.context) return null;
  const context = preview.context;
  if (context.active !== "yes") {
    return { tone: "neutral", title: "Live rider help is on", detail: "No order is on its way, so a rider calling now is told no delivery is expected.", lines: [] };
  }
  const store = LIVE_STORES[context.provider] ?? "Your";
  const address = known(context.delivery_address);
  const status = [known(context.order_status), known(context.eta) && `arriving ${known(context.eta)}`].filter(Boolean).join(" · ");
  if (!address) {
    return { tone: "warning", title: "Live rider help is on", detail: `Your ${store} order${status ? ` (${status})` : ""} has no saved address linked, so a rider calling now can't be guided. Choose the address for this store.`, lines: [] };
  }
  const lines = [
    ["Address", address],
    ["Landmark", known(context.landmark)],
    ["Route", known(context.route_directions)],
    ["Handover", known(context.handover_instructions)],
  ].filter(([, value]) => value).map(([label, value]) => ({ label, value }));
  return { tone: "success", title: "Live rider help is on", detail: `A ${store} rider calling now hears${status ? ` (${status})` : ""}:`, lines };
}
