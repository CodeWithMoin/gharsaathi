// Voice prefill is untrusted model output: keep only known short text fields.
const PREFILL_LIMITS = {
  type: 5,
  name: 80,
  flat_details: 160,
  building_name: 160,
  floor: 40,
  landmark: 160,
  short_address: 200,
  formatted_address: 500,
  contact_name: 100,
};

const ADDRESS_TYPES = new Set(["HOME", "WORK", "OTHER"]);

export function sanitizeAddressPrefill(raw) {
  const prefill = {};
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return prefill;
  for (const [key, limit] of Object.entries(PREFILL_LIMITS)) {
    const value = raw[key];
    if (typeof value !== "string") continue;
    const clean = value.replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/\s+/g, " ").trim().slice(0, limit);
    if (!clean) continue;
    if (key === "type" && !ADDRESS_TYPES.has(clean)) continue;
    prefill[key] = clean;
  }
  return prefill;
}
