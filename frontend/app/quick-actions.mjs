/**
 * Quick actions on the home screen and above the composer, saved lists, the
 * basket card's edit actions and the header's live order chip. Pure logic only;
 * the components are in quick-chips.tsx, saved-lists-dialog.tsx and
 * basket/BasketPanel.tsx.
 *
 * Nothing here predicts a need: chips show only the household's own last order,
 * its own recent products and the lists it saved and named.
 */

export const MAX_CHIPS = 6;
export const MAX_QUANTITY = 50;
export const WHERE_IS_MY_ORDER = "Mera order kahan hai?";

const STORE_LABELS = { swiggy_instamart: "Swiggy Instamart", zepto: "Zepto" };

/**
 * @typedef {{ provider: "swiggy_instamart" | "zepto", product_id: string, variant_id: string, sku_id?: string | null }} Ref
 * @typedef {{ ref: Ref, name: string, brand?: string | null, pack_size?: string | null, quantity: number, store?: string, last_price?: { paise: number, display: string } | null }} ListItem
 * @typedef {{ id: string, name: string, items: ListItem[], item_count: number }} SavedList
 * @typedef {{ provider: "swiggy_instamart" | "zepto", store: string, item_count: number, item_names: string[] }} LastOrder
 * @typedef {{ last_order: LastOrder | null, recent_items: ListItem[], lists: { id: string, name: string, item_count: number }[] }} QuickActions
 * @typedef {{ kind: "repeat" } | { kind: "where" } | { kind: "recent", item: ListItem } | { kind: "lists" }} ChipAction
 * @typedef {{ key: string, label: string, description: string, action: ChipAction }} Chip
 */

/**
 * The chips to show, in order: repeat the last order, "Mera order kahan hai?"
 * (only while an order is moving), up to three recent products, My lists.
 *
 * @param {{ actions: QuickActions | null, orderActive: boolean, max?: number }} input
 * @returns {Chip[]}
 */
export function quickChips({ actions, orderActive, max = MAX_CHIPS }) {
  /** @type {Chip[]} */
  const chips = [];
  const last = actions?.last_order ?? null;
  if (last && last.item_count > 0) {
    chips.push({ key: "repeat", label: "Reorder last order", description: `Put the ${last.item_count} ${last.item_count === 1 ? "item" : "items"} from your last ${last.store} order back in the basket`, action: { kind: "repeat" } });
  }
  if (orderActive) chips.push({ key: "where", label: WHERE_IS_MY_ORDER, description: "Ask where your order is", action: { kind: "where" } });
  const seen = new Set();
  for (const item of actions?.recent_items ?? []) {
    const key = `${item.ref.provider}:${item.ref.variant_id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    if (seen.size > 3) break;
    chips.push({ key: `recent-${key}`, label: shortName(item), description: `Add ${item.name}${item.pack_size ? ` ${item.pack_size}` : ""} to the ${STORE_LABELS[item.ref.provider] ?? "store"} basket`, action: { kind: "recent", item } });
  }
  chips.push({ key: "lists", label: "My lists", description: "Open your saved lists", action: { kind: "lists" } });
  // "My lists" always stays; trim recent items first when space is short.
  while (chips.length > max) {
    const index = chips.findLastIndex((chip) => chip.action.kind === "recent");
    chips.splice(index === -1 ? chips.length - 2 : index, 1);
  }
  return chips;
}

/** @param {ListItem} item */
function shortName(item) {
  const name = item.name.length > 22 ? `${item.name.slice(0, 21).trimEnd()}…` : item.name;
  return `+ ${name}`;
}

/** What the user's chip tap says in the timeline (their own action, as a user turn). */
export function chipUserText(/** @type {Chip} */ chip) {
  if (chip.action.kind === "repeat") return "Reorder my last order";
  if (chip.action.kind === "where") return WHERE_IS_MY_ORDER;
  if (chip.action.kind === "recent") return `Add ${chip.action.item.name}${chip.action.item.pack_size ? ` ${chip.action.item.pack_size}` : ""}`;
  return "My lists";
}

export const GET_BILL_HINT = "Tap “Get final bill” on the basket below when you're ready.";

const SKIP_REASONS = {
  unavailable: (store) => `unavailable at ${store} right now`,
  not_found: (store) => `not listed at ${store} today`,
  store_not_ready: (store) => `${store} isn't connected or has no delivery address`,
  store_error: (store) => `${store} couldn't be reached`,
  basket_locked: () => "basket locked while an order is being checked",
};

/**
 * @typedef {{ name: string, pack_size?: string | null, store: string, provider: string, quantity: number, status: keyof typeof SKIP_REASONS | "added" }} ItemResult
 * @typedef {{ added_count: number, items: ItemResult[], baskets: { provider: "swiggy_instamart" | "zepto", lines: unknown[] }[] }} AddReport
 */

/**
 * Plain-English summary of an add (repeat / list / recent item). Errors are
 * never worded as "not found" (docs/FAILURE_MODES.md rule 1).
 *
 * @param {AddReport} report
 * @param {{ source: string }} options e.g. "your last order", "Weekly list"
 * @returns {{ text: string, added: number, skipped: ItemResult[], providers: ("swiggy_instamart" | "zepto")[], offerBill: boolean }}
 */
export function addReportText(report, { source }) {
  const items = Array.isArray(report?.items) ? report.items : [];
  const added = items.filter((item) => item.status === "added");
  const skipped = items.filter((item) => item.status !== "added");
  const count = added.reduce((total, item) => total + item.quantity, 0);
  const head = added.length
    ? `Added ${count} ${count === 1 ? "item" : "items"} from ${source} to your basket.`
    : `Nothing from ${source} was added.`;
  const reasons = skipped.map((item) => `${item.name} (${(SKIP_REASONS[item.status] ?? SKIP_REASONS.store_error)(item.store)})`);
  const shown = reasons.slice(0, 4);
  const more = reasons.length > shown.length ? `, and ${reasons.length - shown.length} more` : "";
  const tail = reasons.length ? ` Not added: ${shown.join(", ")}${more}.` : "";
  const offerBill = added.length > 0;
  const providers = [...new Set((report?.baskets ?? []).map((basket) => basket.provider))];
  return { text: `${head}${tail}${offerBill ? ` ${GET_BILL_HINT}` : ""}`, added: added.length, skipped, providers, offerBill };
}

/**
 * Undo for a one-tap recent item: the quantity that line had before the add
 * (0 removes it). Null when the line is gone or someone already changed it.
 *
 * @param {{ lines: { ref: Ref, quantity: number }[] } | null | undefined} basket the basket returned by the add
 * @param {Ref} ref
 * @param {number} added
 */
export function undoQuantity(basket, ref, added) {
  const line = basket?.lines?.find((item) => item.ref.variant_id === ref.variant_id);
  if (!line) return null;
  return Math.max(0, line.quantity - added);
}

/**
 * Whether the basket card may be edited or cleared, and the reason when not.
 *
 * @param {{ status: string, lines: unknown[] } | null | undefined} basket
 * @param {{ busy?: boolean }} [options]
 * @returns {{ canEdit: boolean, canClear: boolean, reason: string | null }}
 */
export function basketEditState(basket, { busy = false } = {}) {
  if (!basket) return { canEdit: false, canClear: false, reason: null };
  if (basket.status === "checkout_pending") return { canEdit: false, canClear: false, reason: "Can't change or clear this basket while the store is confirming an order." };
  if (basket.status === "ordered") return { canEdit: false, canClear: false, reason: "This basket was ordered." };
  const empty = !basket.lines?.length;
  return { canEdit: !busy, canClear: !busy && !empty, reason: null };
}

/** The Clear basket confirmation copy. */
export function clearBasketCopy(/** @type {{ provider: string, lines: { quantity: number }[] }} */ basket) {
  const count = (basket?.lines ?? []).reduce((total, line) => total + line.quantity, 0);
  const store = STORE_LABELS[/** @type {"zepto"} */ (basket?.provider)] ?? "store";
  return {
    title: "Clear basket?",
    body: `${count === 1 ? "This removes the 1 item" : `This removes all ${count} items`} from your ${store} basket. Any bill or order card for it stops working.`,
    confirmLabel: "Clear basket",
    busyLabel: "Clearing…",
  };
}

/** A clear that lost to another change (409) reads as a review prompt, not an error. */
export const CLEAR_CONFLICT_TEXT = "This basket changed. Review it and try again.";

/** −/+ on a basket line: the new quantity, or null when the button does nothing. */
export function stepQuantity(/** @type {number} */ quantity, /** @type {-1 | 1} */ delta) {
  const next = quantity + delta;
  if (next < 0 || next > MAX_QUANTITY) return null;
  return next;
}

/** The search a Swap runs for a basket line: its name with the pack size when not in the name. */
export function swapQuery(/** @type {{ name: string, pack_size?: string | null }} */ line) {
  const name = line.name.split(/\s+/).filter(Boolean).join(" ");
  return line.pack_size && !name.toLowerCase().includes(line.pack_size.toLowerCase()) ? `${name} ${line.pack_size}`.slice(0, 120) : name.slice(0, 120);
}

// --- list editing (InputDialog) ---------------------------------------------

/**
 * @typedef {{ listId: string, name: string, items: ListItem[] }} ListDraft
 */

/** @param {SavedList} list @returns {ListDraft} */
export function listDraft(list) {
  return { listId: list.id, name: list.name, items: list.items.map((item) => ({ ...item })) };
}

/** @param {ListDraft} draft @param {number} index @param {-1 | 1} delta @returns {ListDraft} */
export function draftStep(draft, index, delta) {
  const item = draft.items[index];
  if (!item) return draft;
  const next = item.quantity + delta;
  if (next < 1 || next > MAX_QUANTITY) return draft;
  return { ...draft, items: draft.items.map((entry, i) => i === index ? { ...entry, quantity: next } : entry) };
}

/** @param {ListDraft} draft @param {number} index @returns {ListDraft} */
export function draftRemove(draft, index) {
  return { ...draft, items: draft.items.filter((_, i) => i !== index) };
}

/** @param {ListDraft} draft @param {string} name @returns {ListDraft} */
export function draftRename(draft, name) {
  return { ...draft, name };
}

/** @param {ListDraft} draft @param {SavedList} list */
export function draftDirty(draft, list) {
  if (draft.name.trim() !== list.name) return true;
  if (draft.items.length !== list.items.length) return true;
  return draft.items.some((item, index) => item.ref.variant_id !== list.items[index].ref.variant_id || item.quantity !== list.items[index].quantity);
}

/** The PATCH body for a draft; null when the name is invalid. */
export function draftPayload(/** @type {ListDraft} */ draft) {
  const name = draft.name.split(/\s+/).filter(Boolean).join(" ");
  if (!name || name.length > 60) return null;
  return { name, items: draft.items.map((item) => ({ ref: item.ref, quantity: item.quantity })) };
}

/** Validation message for a new list name, or null. */
export function listNameProblem(/** @type {string} */ name, /** @type {{ name: string }[]} */ lists = []) {
  const clean = name.split(/\s+/).filter(Boolean).join(" ");
  if (!clean) return "Give the list a name.";
  if (clean.length > 60) return "Keep the name under 60 characters.";
  if (lists.some((list) => list.name.toLocaleLowerCase() === clean.toLocaleLowerCase())) return `You already have a list called “${clean}”.`;
  return null;
}

// --- header live order chip ---------------------------------------------------

/**
 * @typedef {import("./order-status.mjs").OrderStatusCard} OrderStatusCard
 * @typedef {{ orderId: string, text: string, label: string, tone: "normal" | "late" | "moving" }} HeaderChip
 */

/** @param {OrderStatusCard} card */
export function orderIsActive(card) {
  return card.active !== false && card.status !== "delivered" && card.status !== "cancelled" && card.status !== "unavailable";
}

/**
 * The newest moving order as a short header chip: "Zepto · arriving 8:26",
 * "Out for delivery · 8:26", "Running late" (short enough for a 390 px header;
 * the accessible label names the store). Only what the store
 * reported; null when no order is moving.
 *
 * @param {OrderStatusCard[]} cards
 * @param {number} nowMs
 * @returns {HeaderChip | null}
 */
export function headerOrderChip(cards, nowMs) {
  const card = [...(cards ?? [])].filter(orderIsActive).sort((a, b) => b.seq - a.seq)[0];
  if (!card) return null;
  const store = card.store || "Zepto";
  const eta = card.etaAt ? Date.parse(card.etaAt) : NaN;
  const late = !Number.isNaN(eta) && eta <= nowMs;
  let text;
  /** @type {HeaderChip["tone"]} */
  let tone = "normal";
  if (late) { text = "Running late"; tone = "late"; }
  else if (card.status === "out_for_delivery") { text = Number.isNaN(eta) ? "Out for delivery" : `Out for delivery · ${clock(eta)}`; tone = "moving"; }
  else if (!Number.isNaN(eta)) text = `${store} · arriving ${clock(eta)}`;
  else if (card.status === "preparing") text = `${store} · Being packed`;
  else text = `${store} · Order placed`;
  return { orderId: card.orderId, text, label: `${store} order: ${text.replace(`${store} · `, "")}. Show the order status`, tone };
}

/** "8:26" — compact, for the header. */
function clock(/** @type {number} */ ms) {
  return new Intl.DateTimeFormat("en-IN", { hour: "numeric", minute: "2-digit", hour12: true }).format(new Date(ms)).replace(/\s?[ap]m$/i, "");
}
