/**
 * Settings → Delivery line (Beta), the copy and number only (the directions editor and the
 * shared line live in delivery-line.mjs / delivery-line-panel.tsx): the Sarvam AI number that answers delivery calls the
 * owner declines. Forwarding is "when busy" only (**67*): forwarding unanswered or
 * unreachable calls would loop when the AI transfers the rider back to the owner.
 */

export const DELIVERY_LINE_CONFIG_PATH = "/api/inbound-delivery/config";
/** Used when the server does not report a number. */
export const DELIVERY_LINE_NUMBER = "+918064264240";
export const FORWARDING_OFF_CODE = "##002#";

/** "+918064264240" → "+91 80 6426 4240"; anything else is returned trimmed. */
export function formatDeliveryNumber(number) {
  const digits = String(number ?? "").replace(/[^\d]/g, "");
  if (digits.length === 12 && digits.startsWith("91")) return `+91 ${digits.slice(2, 4)} ${digits.slice(4, 8)} ${digits.slice(8)}`;
  return String(number ?? "").trim();
}

/** Busy-only forwarding code, e.g. "**67*+918064264240#". */
export function forwardingCode(number) {
  const digits = String(number ?? "").replace(/[^\d]/g, "");
  return `**67*+${digits}#`;
}

/**
 * @param {{ enabled?: boolean, inbound_phone_number?: string | null } | null | undefined} config
 * `undefined` = still loading, `null` = could not load.
 */
export function deliveryLineSettingsView(config) {
  const number = (config && typeof config.inbound_phone_number === "string" && config.inbound_phone_number.trim()) || DELIVERY_LINE_NUMBER;
  const status = config === undefined
    ? "Checking…"
    : config === null
      ? "Couldn't check right now"
      : config.enabled
        ? "On"
        : "Not set up on this server yet";
  return { number, display: formatDeliveryNumber(number), forwarding: forwardingCode(number), off: FORWARDING_OFF_CODE, status, ready: config?.enabled === true };
}
