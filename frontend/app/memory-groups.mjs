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
  product_name: "Product",
  diet: "Diet",
  ingredient: "Keep out of food",
  style: "Value preference",
  fact: "Answer",
  note: "Delivery note",
};

/** Fields whose value is one of a fixed set; the edit popup shows a choice. */
export const FIELD_OPTIONS = {
  diet: [
    { value: "vegetarian", label: "Vegetarian" },
    { value: "eggetarian", label: "Vegetarian, eggs are fine" },
    { value: "jain", label: "Jain food" },
    { value: "non_vegetarian", label: "Non-vegetarian" },
  ],
  style: [
    { value: "cheapest", label: "Cheapest first" },
    { value: "balanced", label: "Balance price and quality" },
    { value: "premium", label: "Premium brands" },
  ],
};
const FACT_OPTIONS = {
  people: Array.from({ length: 15 }, (_, index) => ({ value: String(index + 1), label: String(index + 1) })),
  kids: [{ value: "yes", label: "Yes" }, { value: "no", label: "No" }],
  baby: [{ value: "yes", label: "Yes" }, { value: "no", label: "No" }],
  pets: [
    { value: "none", label: "None" },
    { value: "dog", label: "Dog" },
    { value: "cat", label: "Cat" },
    { value: "dog_and_cat", label: "Dog and cat" },
    { value: "other", label: "Other" },
  ],
};

const REQUIRED_FIELDS = new Set(["brand", "pack_size", "product", "store", "provider", "quantity", "preference", "rule", "diet", "ingredient", "style", "fact", "note"]);

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

/** @param {{ memory_type: string, value?: any }} memory */
export function groupFor(memory) {
  const type = String(memory.memory_type ?? "").toLowerCase();
  const kind = memory?.value && typeof memory.value === "object" ? memory.value.kind : undefined;
  if (kind === "household" || kind === "delivery_note") return "niyam";
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
    if (v.quantity) rest += `, usually ${v.quantity}`;
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

/** Fields a person can edit inline (with choices for fixed-set values); falls back to the free text. */
export function editableFields(memory) {
  const kind = memory?.value && typeof memory.value === "object" ? memory.value.kind : undefined;
  const v = values(memory);
  const fields = Object.entries(v)
    // A household fact's item names the question ("people"), not something to retype.
    .filter(([key]) => FIELD_LABELS[key] && !(kind === "household" && key === "product_category"))
    .map(([key, value]) => {
      const options = key === "fact" ? FACT_OPTIONS[v.product_category] : FIELD_OPTIONS[key];
      const label = key === "fact" ? HOUSEHOLD_LABELS[v.product_category] ?? FIELD_LABELS.fact : FIELD_LABELS[key];
      return options ? { key, label, value, options } : { key, label, value };
    });
  if (fields.length) return fields;
  return [{ key: "text", label: "Note", value: clean(memory.text) }];
}

const HOUSEHOLD_LABELS = { people: "People at home", kids: "Children at home", baby: "Baby at home", pets: "Pets" };

/** Summary groups in screen order (backend app/memory/summary.py GROUP_TITLES). */
export const SUMMARY_GROUPS = /** @type {const} */ ([
  { id: "home", title: "Home" },
  { id: "food", title: "Food" },
  { id: "usual_buys", title: "Usual buys" },
  { id: "avoid", title: "Avoid" },
  { id: "shopping_style", title: "Shopping style" },
  { id: "from_orders", title: "From your orders" },
]);

export const SOURCE_LABELS = {
  told: "You told us",
  in_passing: "Said in passing",
  repeated_choice: "From your repeated choices",
  order_history: "From your orders",
};

/**
 * Screen rows for "What GharSaathi knows" from GET /api/memory/summary, joined
 * with the raw records (GET /api/memory) that Edit, Undo and Delete need.
 * Unknown groups, sources and malformed items are dropped.
 */
export function summaryView(summary, records, now = Date.now()) {
  const byId = new Map((Array.isArray(records) ? records : []).filter((record) => record && record.id).map((record) => [String(record.id), record]));
  const toRow = (item) => {
    if (!item || typeof item.id !== "string" || typeof item.label !== "string" || !item.label.trim()) return null;
    const managedBy = item.managed_by === "profile" || item.managed_by === "order" ? item.managed_by : "memory";
    const record = managedBy === "memory" ? byId.get(item.id) : undefined;
    if (managedBy === "memory" && !record) return null;
    const source = SOURCE_LABELS[item.source] ? item.source : "told";
    const time = item.updated_at ? Date.parse(item.updated_at) : NaN;
    const recent = record ? isRecentlyLearned(record, now) : false;
    return {
      id: item.id,
      label: item.label.trim(),
      kind: typeof item.kind === "string" ? item.kind : null,
      source,
      badge: recent ? BADGES.stated_recent : SOURCE_LABELS[source],
      date: Number.isFinite(time) ? formatMemoryDate(time, now) : "",
      managedBy,
      value: typeof item.value === "string" ? item.value : null,
      record: record ?? null,
      undoable: record ? canUndo(record, now) : false,
      editable: managedBy === "profile" || (record ? editableFields(record).length > 0 : false),
      deletable: managedBy !== "order",
    };
  };
  const incoming = new Map((Array.isArray(summary?.groups) ? summary.groups : []).map((group) => [group?.id, group]));
  const groups = SUMMARY_GROUPS.map((group) => ({
    ...group,
    rows: (Array.isArray(incoming.get(group.id)?.items) ? incoming.get(group.id).items : []).map(toRow).filter(Boolean),
  })).filter((group) => group.rows.length);
  const suggestions = (Array.isArray(summary?.suggestions) ? summary.suggestions : []).map(toRow).filter(Boolean);
  const total = groups.reduce((sum, group) => sum + group.rows.length, 0);
  const deletable = groups.reduce((sum, group) => sum + group.rows.filter((row) => row.managedBy === "memory").length, 0) + suggestions.length;
  return { groups, suggestions, total, deletable, consent: summary?.consent?.saved_preferences === "on" ? "on" : "off", history: summary?.consent?.purchase_history === "on" ? "on" : "off" };
}

/** PUT /api/profile body that removes one profile answer (back to the default). */
export function profileResetPayload(kind) {
  if (kind === "first_name") return { first_name: null };
  if (kind === "language") return { language: "hinglish" };
  if (kind === "address_form") return { address_form: "neutral" };
  return null;
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
