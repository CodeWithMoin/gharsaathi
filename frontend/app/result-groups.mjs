/**
 * Product results are grouped per searched item ("Atta", "Dahi"), so a
 * multi-item request shows one card per item instead of each search
 * replacing the last. Searching the same item again replaces its group.
 *
 * The voice backend sends `item_key` / `item_label` with each result. When
 * they are missing, the key and label come from the search query, then the
 * first product's name.
 */

export const MAX_RESULT_GROUPS = 20;
export const MAX_GROUP_PRODUCTS = 50;

/**
 * @template P
 * @typedef {{ key: string, label: string, query: string, products: P[], status: "searching" | "done", afterMessageId: string | null, seq: number }} ResultGroup
 */

/** @param {unknown} value */
const text = (value) => (typeof value === "string" ? value.normalize("NFKC").replace(/\s+/g, " ").trim() : "");

/**
 * "Fortune  Cooking-Oil!" -> "fortune cooking oil"
 *
 * @param {unknown} value
 */
export function normalizeItemKey(value) {
  return text(value).toLocaleLowerCase().replace(/[^\p{L}\p{M}\p{N}]+/gu, " ").trim().slice(0, 64);
}

/** @param {string} value */
function labelFrom(value) {
  const clean = text(value).slice(0, 40).trim();
  return clean ? clean.charAt(0).toLocaleUpperCase() + clean.slice(1) : "";
}

/**
 * @param {{ itemKey?: unknown, itemLabel?: unknown, query?: unknown, products?: { name?: unknown, category?: unknown }[] }} source
 * @returns {{ key: string, label: string }}
 */
export function resultGroupIdentity({ itemKey, itemLabel, query, products = [] }) {
  const first = products[0];
  const fromProduct = text(first?.category) || text(first?.name);
  const key = normalizeItemKey(itemKey) || normalizeItemKey(query) || normalizeItemKey(fromProduct) || "results";
  const label = labelFrom(text(itemLabel)) || labelFrom(text(query)) || labelFrom(fromProduct) || "Results";
  return { key, label };
}

/**
 * Adds a group, or replaces the group for the same item. The new group is the
 * newest, so it moves to where it arrived.
 *
 * @template P
 * @param {ResultGroup<P>[]} groups
 * @param {ResultGroup<P>} group
 * @returns {ResultGroup<P>[]}
 */
export function upsertResultGroup(groups, group) {
  return [...groups.filter((item) => item.key !== group.key), group].slice(-MAX_RESULT_GROUPS);
}

/**
 * Updates a group in place (a typed search finishing), keeping its position.
 *
 * @template P
 * @param {ResultGroup<P>[]} groups
 * @param {string} key
 * @param {Partial<Pick<ResultGroup<P>, "products" | "status">>} patch
 * @returns {ResultGroup<P>[]}
 */
export function updateResultGroup(groups, key, patch) {
  return groups.map((group) => (group.key === key ? { ...group, ...patch } : group));
}

/**
 * @template P
 * @param {ResultGroup<P>[]} groups
 * @param {string} key
 */
export function removeResultGroup(groups, key) {
  return groups.filter((group) => group.key !== key);
}

/**
 * Header line for one group, from its comparison-option count.
 *
 * @param {number} optionCount
 * @param {number} shownLimit options shown before "show more"
 */
export function resultsHeadline(optionCount, shownLimit) {
  if (optionCount === 1) return "1 relevant option found.";
  if (optionCount > shownLimit) return `${optionCount} options found—showing the top ${shownLimit}.`;
  return `${optionCount} relevant options found.`;
}

/**
 * "Atta · 3 options"
 *
 * @param {string} label
 * @param {number} optionCount
 */
export function groupBadge(label, optionCount) {
  return `${label} · ${optionCount} ${optionCount === 1 ? "option" : "options"}`;
}

/**
 * Saved form. Groups still searching are not saved: reopened, they would
 * spin forever.
 *
 * @template P
 * @param {ResultGroup<P>[]} groups
 */
export function resultGroupsSnapshot(groups) {
  return groups
    .filter((group) => group.status === "done")
    .slice(-MAX_RESULT_GROUPS)
    .map((group) => ({
      item_key: group.key,
      item_label: group.label,
      query: group.query.slice(0, 120),
      products: group.products.slice(0, MAX_GROUP_PRODUCTS),
      after_message_id: group.afterMessageId,
      seq: group.seq,
    }));
}

/** @param {unknown} value */
const count = (value) => (typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null);

/**
 * Reads saved groups. A chat saved before groups existed (one `products` list
 * placed after `results_after_message_id`) loads as a single group.
 *
 * @template P
 * @param {{ resultGroups?: unknown, products?: P[], searched?: boolean, submittedQuery?: string, resultsAfterMessageId?: string | null, resultsSeq?: number }} saved
 * @returns {ResultGroup<P>[]}
 */
export function restoreResultGroups({ resultGroups, products = [], searched = false, submittedQuery = "", resultsAfterMessageId = null, resultsSeq = 0 }) {
  if (Array.isArray(resultGroups) && resultGroups.length) {
    /** @type {ResultGroup<P>[]} */
    let groups = [];
    for (const raw of resultGroups) {
      if (!raw || typeof raw !== "object") continue;
      const value = /** @type {Record<string, unknown>} */ (raw);
      const items = /** @type {P[]} */ (Array.isArray(value.products) ? value.products : []);
      const query = text(value.query);
      const identity = resultGroupIdentity({ itemKey: value.item_key, itemLabel: value.item_label, query, products: /** @type {never[]} */ (items) });
      const anchor = value.after_message_id;
      groups = upsertResultGroup(groups, {
        ...identity,
        query,
        products: items.slice(0, MAX_GROUP_PRODUCTS),
        status: "done",
        afterMessageId: typeof anchor === "string" && anchor ? anchor : null,
        seq: count(value.seq) ?? 0,
      });
    }
    return groups;
  }
  if (!products.length && !searched) return [];
  const query = text(submittedQuery);
  return [{
    ...resultGroupIdentity({ query, products: /** @type {never[]} */ (products) }),
    query,
    products: products.slice(0, MAX_GROUP_PRODUCTS),
    status: "done",
    afterMessageId: resultsAfterMessageId,
    seq: resultsSeq,
  }];
}
