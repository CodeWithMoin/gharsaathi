/**
 * Settings → Delivery line (Beta), the copy and number only (the directions editor and the
 * shared line live in delivery-line.mjs / delivery-line-panel.tsx): the Sarvam AI number that answers delivery calls the
 * owner declines. Forwarding is "when busy" only (**67*): forwarding unanswered or
 * unreachable calls would loop when the AI transfers the rider back to the owner.
 */

export const DELIVERY_LINE_CONFIG_PATH = "/api/inbound-delivery/config";
export const FORWARDING_OFF_CODE = "##002#";

/** "+919999999999" → "+91 99 9999 9999"; anything else is returned trimmed. */
export function formatDeliveryNumber(number) {
  const digits = String(number ?? "").replace(/[^\d]/g, "");
  if (digits.length === 12 && digits.startsWith("91")) return `+91 ${digits.slice(2, 4)} ${digits.slice(4, 8)} ${digits.slice(8)}`;
  return String(number ?? "").trim();
}

/** Busy-only forwarding code, e.g. "**67*+919999999999#". */
export function forwardingCode(number) {
  const digits = String(number ?? "").replace(/[^\d]/g, "");
  return digits ? `**67*+${digits}#` : "";
}

/**
 * @param {{ enabled?: boolean, inbound_phone_number?: string | null } | null | undefined} config
 * `undefined` = still loading, `null` = could not load.
 */
export function deliveryLineSettingsView(config) {
  const number = (config && typeof config.inbound_phone_number === "string" && config.inbound_phone_number.trim()) || "";
  const status = config === undefined
    ? "Checking…"
    : config === null
      ? "Couldn't check right now"
      : config.enabled
        ? "On"
        : "Not set up on this server yet";
  return { number, display: formatDeliveryNumber(number) || "Not configured", forwarding: forwardingCode(number), off: FORWARDING_OFF_CODE, status, ready: config?.enabled === true };
}
