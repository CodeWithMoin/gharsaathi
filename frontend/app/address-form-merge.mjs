// Merging assistant prefill into the open address form, and the screen
// acknowledgement the backend waits for before the assistant may say "open".

export const ADDRESS_TEXT_FIELDS = ["name", "flat_details", "building_name", "floor", "formatted_address", "short_address", "landmark", "contact_name", "contact_number"];
// Fields the assistant may prefill; contact_number never comes from the model.
const PREFILLABLE = new Set(["type", "name", "flat_details", "building_name", "floor", "formatted_address", "short_address", "landmark", "contact_name"]);
export const REQUIRED_FOCUS_ORDER = ["flat_details", "building_name", "formatted_address", "short_address", "name", "contact_name", "contact_number"];
export const ACK_TIMEOUT_MS = 4000;

/**
 * Fill only fields the user has not edited. A value the assistant set earlier
 * may be replaced by a newer one from the user's own words.
 * @param {Record<string, string>} values current form values
 * @param {Record<string, string>} incoming sanitised prefill
 * @param {Set<string>} touched fields the user edited
 * @returns {{ values: Record<string, string>, filled: string[], kept: string[] }}
 */
export function mergeAddressPrefill(values, incoming, touched) {
  const next = { ...values };
  const filled = [];
  const kept = [];
  for (const [key, raw] of Object.entries(incoming ?? {})) {
    if (!PREFILLABLE.has(key) || typeof raw !== "string" || !raw.trim()) continue;
    const value = raw.trim();
    if (touched.has(key)) {
      if ((values[key] ?? "") !== value) kept.push(key);
      continue;
    }
    if ((values[key] ?? "") === value) continue;
    next[key] = value;
    filled.push(key);
  }
  return { values: next, filled: filled.sort(), kept: kept.sort() };
}

/** @param {Record<string, string>} values */
export function firstEmptyField(values) {
  return REQUIRED_FOCUS_ORDER.find((key) => !(values[key] ?? "").trim()) ?? null;
}

/**
 * Turns whatever the address-form handler returned into an acknowledgement.
 * Never rejects and never stays pending: a missing handler, a throw or no
 * answer in time all report form_visible false.
 * @param {unknown} result
 * @param {{ timeoutMs?: number }} [options]
 */
export function settleAddressFormAck(result, options = {}) {
  const timeoutMs = options.timeoutMs ?? ACK_TIMEOUT_MS;
  const notShown = (reason) => ({ form_visible: false, reason, filled_fields: [], kept_user_fields: [] });
  if (result === undefined || result === null) return Promise.resolve(notShown("not_handled"));
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(notShown("render_timeout")), timeoutMs);
    Promise.resolve(result).then(
      (value) => {
        clearTimeout(timer);
        if (!value || typeof value !== "object" || value.form_visible !== true) {
          resolve(notShown(value && typeof value.reason === "string" ? value.reason : "render_failed"));
          return;
        }
        resolve({
          form_visible: true,
          reason: "shown",
          filled_fields: Array.isArray(value.filled_fields) ? value.filled_fields.filter((key) => PREFILLABLE.has(key)) : [],
          kept_user_fields: Array.isArray(value.kept_user_fields) ? value.kept_user_fields.filter((key) => PREFILLABLE.has(key)) : [],
        });
      },
      () => {
        clearTimeout(timer);
        resolve(notShown("render_failed"));
      },
    );
  });
}
