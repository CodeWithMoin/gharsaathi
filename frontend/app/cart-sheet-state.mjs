/**
 * Rules behind the cart bottom sheet (`cart-sheet.tsx`): which store tab shows,
 * when "Get final bill" can run and why not, the empty state, the drag-to-close
 * decision, and what a ready bill does (close the sheet, put the bill card in the chat).
 */

/** @typedef {"swiggy_instamart" | "zepto"} Store */
/** @typedef {{ provider: Store, status: string, address_id?: string | null, lines: { quantity: number }[] }} SheetBasket */
/** @typedef {{ open: boolean, tab: Store | null }} SheetState */

/** Tab order: Zepto first, then Instamart (matches the header cart). */
export const SHEET_STORES = /** @type {const} */ (["zepto", "swiggy_instamart"]);
export const STORE_TAB_LABELS = Object.freeze({ zepto: "Zepto", swiggy_instamart: "Instamart" });
export const STORE_NAMES = Object.freeze({ zepto: "Zepto", swiggy_instamart: "Swiggy Instamart" });
export const FEES_NOTE = "Fees and final total on the bill";
export const EMPTY_TITLE = "Your basket is empty";

const CLOSED_BASKET = new Set(["ordered", "abandoned"]);

/** @type {SheetState} */
export const CLOSED_SHEET = Object.freeze({ open: false, tab: null });

/** Items in one store's open basket (an ordered basket counts as empty). */
export function itemCount(/** @type {SheetBasket | null | undefined} */ basket) {
  if (!basket || CLOSED_BASKET.has(basket.status)) return 0;
  return basket.lines.reduce((total, line) => total + line.quantity, 0);
}

/**
 * Stores that have items, in tab order, with their counts.
 * @param {Partial<Record<Store, SheetBasket | undefined>>} baskets
 * @returns {{ provider: Store, label: string, count: number }[]}
 */
export function sheetStores(baskets) {
  return SHEET_STORES
    .map((provider) => ({ provider, label: STORE_TAB_LABELS[provider], count: itemCount(baskets[provider]) }))
    .filter((store) => store.count > 0);
}

/**
 * The tab to show: the current one while it still has items, else the preferred
 * store, else the first store with items; null when the basket is empty.
 * @param {Partial<Record<Store, SheetBasket | undefined>>} baskets
 * @param {Store | null | undefined} current
 * @param {Store | null | undefined} [preferred]
 * @returns {Store | null}
 */
export function pickTab(baskets, current, preferred) {
  const stores = sheetStores(baskets).map((store) => store.provider);
  if (current && stores.includes(current)) return current;
  if (preferred && stores.includes(preferred)) return preferred;
  return stores[0] ?? null;
}

/**
 * What the sheet renders right now.
 * @param {Partial<Record<Store, SheetBasket | undefined>>} baskets
 * @param {Store | null | undefined} tab
 */
export function sheetView(baskets, tab) {
  const stores = sheetStores(baskets);
  const active = pickTab(baskets, tab);
  return {
    stores,
    /** Store tabs only when both stores have items. */
    showTabs: stores.length > 1,
    tab: active,
    empty: active === null,
    totalCount: stores.reduce((total, store) => total + store.count, 0),
  };
}

/**
 * Open / close / tab changes.
 * @param {SheetState} state
 * @param {{ type: "open" | "toggle", baskets: Partial<Record<Store, SheetBasket | undefined>>, preferred?: Store | null } | { type: "close" } | { type: "select", provider: Store, baskets: Partial<Record<Store, SheetBasket | undefined>> }} action
 * @returns {SheetState}
 */
export function sheetReducer(state, action) {
  switch (action.type) {
    case "open":
      return { open: true, tab: pickTab(action.baskets, null, action.preferred) };
    case "toggle":
      return state.open ? { ...state, open: false } : { open: true, tab: pickTab(action.baskets, null, action.preferred) };
    case "close":
      return state.open ? { ...state, open: false } : state;
    case "select":
      return sheetStores(action.baskets).some((store) => store.provider === action.provider) ? { ...state, tab: action.provider } : state;
    default:
      return state;
  }
}

/**
 * Whether "Get final bill" can run for this store, and the reason shown when not.
 * `reason` is null while it is only busy (a request is already running).
 *
 * @param {{
 *   basket: SheetBasket | null | undefined,
 *   connected: boolean,
 *   addressId: string | null | undefined,
 *   orderInFlight?: boolean,
 *   busy?: boolean,
 * }} input
 * @returns {{ disabled: boolean, reason: string | null }}
 */
export function billAvailability({ basket, connected, addressId, orderInFlight = false, busy = false }) {
  if (!basket) return { disabled: true, reason: null };
  const store = STORE_NAMES[basket.provider] ?? "the store";
  if (basket.status === "ordered") return { disabled: true, reason: "This basket was ordered." };
  if (basket.status === "checkout_pending") return { disabled: true, reason: `${store} is still confirming an order for this basket. Wait for it before getting a new bill.` };
  if (orderInFlight) return { disabled: true, reason: `We don't know yet if your last ${store} order went through. Check its card in the chat first.` };
  if (itemCount(basket) === 0) return { disabled: true, reason: "Add an item first." };
  if (!addressId) return { disabled: true, reason: connected ? `Choose a ${store} delivery address to get the bill.` : `${store} isn't connected. Connect it in Settings to get the bill.` };
  if (busy) return { disabled: true, reason: null };
  return { disabled: false, reason: null };
}

/**
 * Quick chips for the empty state: only the recent items (one tap adds one).
 * @template {{ action: { kind: string } }} C
 * @param {C[]} chips
 * @returns {C[]}
 */
export function emptyStateChips(chips) {
  return chips.filter((chip) => chip.action.kind === "recent");
}

/**
 * A bill came back: the sheet closes and the bill card goes to the bottom of the chat.
 * @template R
 * @param {SheetState} state
 * @param {Store} provider
 * @param {R} request
 * @returns {{ state: SheetState, emit: { kind: "bill", provider: Store, stage: "bill_ready", request: R } }}
 */
export function afterBill(state, provider, request) {
  return { state: { ...state, open: false }, emit: { kind: "bill", provider, stage: "bill_ready", request } };
}

// --- drag to close ---------------------------------------------------------

/** Apple's momentum projection: where a flick at `velocity` (px/s) would come to rest. */
export function projectMomentum(/** @type {number} */ velocity, decelerationRate = 0.998) {
  return (velocity / 1000) * decelerationRate / (1 - decelerationRate);
}

/**
 * Close after a drag? Uses the projected resting point, so a quick flick down is
 * enough and a slow drag must pass half the sheet. A flick up always keeps it open.
 * @param {{ offset: number, velocity: number, height: number }} input offset/velocity in px and px/s, down is positive
 */
export function shouldDismiss({ offset, velocity, height }) {
  if (!Number.isFinite(offset) || !Number.isFinite(height) || height <= 0) return false;
  const speed = Number.isFinite(velocity) ? velocity : 0;
  if (speed < -200) return false;
  return offset + projectMomentum(Math.max(0, speed)) > height * 0.5;
}
