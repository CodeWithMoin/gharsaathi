/**
 * Pure helpers for the "Saved preferences" settings screen: grouping, readable
 * English sentences, origin badges, the undo window and the
 * correction payload for inline edits. No I/O; safe to unit test.
 */

export const UNDO_WINDOW_MS = 24 * 60 * 60 * 1000;

export const GROUPS = /** @type {const} */ ([
  { id: "pasand", title: "Preferences" },
  { id: "kabhi_nahi", title: "Never" },
  { id: "niyam", title: "Rules" },
]);

export const BADGES = {
  stated_recent: "Just learned",
  stated: "You told us",
  explicit: "You told us",
  inferred: "Suggested",
};

const PRODUCT_WORDS = {
  milk: "Milk",
  doodh: "Milk",
  "cooking oil": "Cooking oil",
  oil: "Cooking oil",
  tel: "Cooking oil",
  atta: "Atta",
  flour: "Atta",
  "wheat flour": "Atta",
  rice: "Rice",
  chawal: "Rice",
  tea: "Tea",
  chai: "Tea",
  soap: "Soap",
  sabun: "Soap",
  egg: "Eggs",
  eggs: "Eggs",
  salt: "Salt",
  namak: "Salt",
  sugar: "Sugar",
  cheeni: "Sugar",
  curd: "Curd",
  dahi: "Curd",
  bread: "Bread",
};

const STORE_NAMES = {
  swiggy: "Swiggy Instamart",
  swiggy_instamart: "Swiggy Instamart",
  instamart: "Swiggy Instamart",
  zepto: "Zepto",
};

const FIELD_LABELS = {
  brand: "Brand",
  alternative_brand: "Other brand",
  product_category: "Item",
  product: "Product",
  pack_size: "Pack size",
  quantity: "Quantity",
  unit: "Unit",
  store: "Store",
  provider: "Store",
  preference: "Rule",
  rule: "Rule",
  reason: "Reason",
};

const REQUIRED_FIELDS = new Set(["brand", "pack_size", "product", "store", "provider", "quantity", "preference", "rule"]);

/** @param {unknown} value */
function clean(value) {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value !== "string") return "";
  return value.trim().replace(/\s+/g, " ");
}

/** @param {string} text */
function capitalize(text) {
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}

/** English display word for a product/category, e.g. "doodh" -> "Milk". */
export function productLabel(value) {
  const text = clean(value);
  if (!text) return "";
  return PRODUCT_WORDS[text.toLowerCase()] ?? capitalize(text);
}

export function storeLabel(value) {
  const text = clean(value);
  if (!text) return "";
  return STORE_NAMES[text.toLowerCase()] ?? capitalize(text);
}

/** @param {{ memory_type: string }} memory */
export function groupFor(memory) {
  const type = String(memory.memory_type ?? "").toLowerCase();
  if (type.includes("avoid")) return "kabhi_nahi";
  if (type === "dietary_preference" || type.includes("rule")) return "niyam";
  if (type.endsWith("_preference")) return "pasand";
  return "niyam";
}

function values(memory) {
  const raw = memory && typeof memory.value === "object" && memory.value !== null ? memory.value : {};
  /** @type {Record<string, string>} */
  const out = {};
  for (const [key, value] of Object.entries(raw)) {
    const text = clean(value);
    if (text) out[key] = text;
  }
  return out;
}

/** A short readable sentence such as "Milk: always Amul Taaza". */
export function memorySentence(memory) {
  const type = String(memory.memory_type ?? "").toLowerCase();
  const v = values(memory);
  const subject = productLabel(v.product_category ?? v.product ?? "");
  const store = storeLabel(v.store ?? v.provider ?? "");
  const withSubject = (fallback, rest) => `${subject || fallback}: ${rest}`;

  if (type === "brand_preference" && v.brand) {
    let rest = `always ${v.brand}`;
    if (v.alternative_brand) rest += ` or ${v.alternative_brand}`;
    if (v.pack_size) rest += `, ${v.pack_size}`;
    return withSubject("Brand", rest);
  }
  if (type === "pack_size_preference" && v.pack_size) {
    return withSubject("Pack size", `always ${v.pack_size}`);
  }
  if (type === "quantity_preference" && v.quantity) {
    return withSubject("Quantity", `usually ${v.quantity}${v.unit ? ` ${v.unit}` : ""}`);
  }
  if (type === "store_preference" && store) {
    return subject ? `${subject}: always from ${store}` : `Store: ${store} first`;
  }
  if (type === "store_avoidance" && store) {
    return subject ? `${subject}: never from ${store}` : `${store}: never`;
  }
  if (type.includes("avoid")) {
    const product = productLabel(v.product ?? "");
    const category = productLabel(v.product_category ?? "");
    if (v.brand && category) return `${category}: never ${v.brand}`;
    if (v.brand && product) return `${product}: never ${v.brand}`;
    if (product || v.brand) return `${product || v.brand}: never buy`;
  }
  if (type === "dietary_preference" && v.preference) return `Diet: ${v.preference}`;
  if (v.rule) return capitalize(v.rule);
  return capitalize(clean(memory.text)) || "Saved preference";
}

/** The moment GharSaathi learned this memory, as epoch ms, or null. */
export function learnedAt(memory) {
  for (const key of ["learned_at", "confirmed_at", "created_at"]) {
    const time = memory?.[key] ? Date.parse(memory[key]) : NaN;
    if (Number.isFinite(time)) return time;
  }
  return null;
}

/** Missing or unknown origin is treated as something the user told us. */
export function originOf(memory) {
  const origin = memory?.origin;
  return origin === "stated" || origin === "explicit" || origin === "inferred" ? origin : "explicit";
}

export function isRecentlyLearned(memory, now = Date.now()) {
  if (originOf(memory) !== "stated") return false;
  const time = learnedAt(memory);
  return time !== null && now - time >= 0 && now - time < UNDO_WINDOW_MS;
}

export function badgeFor(memory, now = Date.now()) {
  const origin = originOf(memory);
  if (origin === "stated") return isRecentlyLearned(memory, now) ? BADGES.stated_recent : BADGES.stated;
  return BADGES[origin];
}

export function canUndo(memory, now = Date.now()) {
  return memory?.status === "confirmed" && isRecentlyLearned(memory, now);
}

function isActive(memory, now) {
  if (memory.deleted_at) return false;
  if (memory.expires_at) {
    const expires = Date.parse(memory.expires_at);
    if (Number.isFinite(expires) && expires <= now) return false;
  }
  return true;
}

export function formatMemoryDate(time, now = Date.now()) {
  if (time === null) return "";
  const date = new Date(time);
  const sameYear = date.getFullYear() === new Date(now).getFullYear();
  return date.toLocaleDateString("en-IN", { day: "numeric", month: "short", ...(sameYear ? {} : { year: "numeric" }) });
}

/**
 * Split raw memory records into confirmed groups and pending suggestions.
 * Rejected, superseded, deleted and expired records are omitted.
 */
export function groupMemories(memories, now = Date.now()) {
  /** @type {Record<string, any[]>} */
  const groups = { pasand: [], kabhi_nahi: [], niyam: [] };
  const suggestions = [];
  for (const memory of Array.isArray(memories) ? memories : []) {
    if (!memory || !isActive(memory, now)) continue;
    const time = learnedAt(memory);
    const row = {
      id: String(memory.id),
      memory,
      group: groupFor(memory),
      sentence: memorySentence(memory),
      badge: badgeFor(memory, now),
      date: formatMemoryDate(time, now),
      learnedAt: time,
      undoable: canUndo(memory, now),
    };
    if (memory.status === "confirmed") groups[row.group].push(row);
    else if (memory.status === "proposed") suggestions.push(row);
  }
  const newestFirst = (a, b) => (b.learnedAt ?? 0) - (a.learnedAt ?? 0);
  for (const list of Object.values(groups)) list.sort(newestFirst);
  suggestions.sort(newestFirst);
  const total = groups.pasand.length + groups.kabhi_nahi.length + groups.niyam.length;
  return {
    groups: GROUPS.map((group) => ({ ...group, rows: groups[group.id] })).filter((group) => group.rows.length),
    suggestions,
    total,
  };
}

/** Text fields a person can edit inline; falls back to the free text. */
export function editableFields(memory) {
  const fields = Object.entries(values(memory))
    .filter(([key]) => FIELD_LABELS[key])
    .map(([key, value]) => ({ key, label: FIELD_LABELS[key], value }));
  if (fields.length) return fields;
  return [{ key: "text", label: "Note", value: clean(memory.text) }];
}

/**
 * Build the body for POST /api/memory/{id}/correct, or null when nothing
 * changed or a required field was blanked.
 */
export function correctionPayload(memory, edits) {
  const fields = editableFields(memory);
  const textOnly = fields.length === 1 && fields[0].key === "text";
  const nextValue = { ...(memory.value ?? {}) };
  let changed = false;
  for (const field of fields) {
    const next = clean(edits?.[field.key] ?? field.value);
    if (next !== field.value) changed = true;
    if (textOnly) continue;
    if (!next) {
      if (REQUIRED_FIELDS.has(field.key)) return null;
      delete nextValue[field.key];
    } else nextValue[field.key] = next;
  }
  if (!changed) return null;
  const text = textOnly ? clean(edits?.text) : memorySentence({ ...memory, value: nextValue });
  if (text.length < 2) return null;
  return {
    memory_type: memory.memory_type,
    value: nextValue,
    text: text.slice(0, 2000),
    source_type: "correction",
    source_conversation_id: memory.source_conversation_id ?? null,
    sensitivity: memory.sensitivity ?? "low",
    confidence: 1,
    allowed_purposes: Array.isArray(memory.allowed_purposes) && memory.allowed_purposes.length ? memory.allowed_purposes : ["personalize_search"],
    reason: "Edited in settings",
  };
}

/** Summarise the preference-memory permission rows for the toggle. */
export function preferencePermissionState(permissions, categories, purpose = "personalize_search") {
  const relevant = (Array.isArray(permissions) ? permissions : []).filter((item) => item && item.purpose === purpose && categories.includes(item.data_category));
  const allowed = categories.filter((category) => relevant.some((item) => item.data_category === category && item.allowed && !item.revoked_at));
  if (allowed.length === 0) return "off";
  return allowed.length === categories.length ? "on" : "partial";
}
