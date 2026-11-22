export type Store = "swiggy_instamart" | "zepto";
export type SheetBasket = { provider: Store; status: string; address_id?: string | null; lines: { quantity: number }[] };
export type SheetState = { open: boolean; tab: Store | null };
type Baskets = Partial<Record<Store, SheetBasket | undefined>>;
export type SheetAction =
  | { type: "open" | "toggle"; baskets: Baskets; preferred?: Store | null }
  | { type: "close" }
  | { type: "select"; provider: Store; baskets: Baskets };

export const SHEET_STORES: readonly ["zepto", "swiggy_instamart"];
export const STORE_TAB_LABELS: Readonly<Record<Store, string>>;
export const STORE_NAMES: Readonly<Record<Store, string>>;
export const FEES_NOTE: string;
export const EMPTY_TITLE: string;
export const CLOSED_SHEET: Readonly<SheetState>;
export function itemCount(basket: SheetBasket | null | undefined): number;
export function sheetStores(baskets: Baskets): { provider: Store; label: string; count: number }[];
export function pickTab(baskets: Baskets, current: Store | null | undefined, preferred?: Store | null): Store | null;
export function sheetView(baskets: Baskets, tab: Store | null | undefined): { stores: { provider: Store; label: string; count: number }[]; showTabs: boolean; tab: Store | null; empty: boolean; totalCount: number };
export function sheetReducer(state: SheetState, action: SheetAction): SheetState;
export function billAvailability(input: { basket: SheetBasket | null | undefined; connected: boolean; addressId: string | null | undefined; orderInFlight?: boolean; busy?: boolean }): { disabled: boolean; reason: string | null };
export function emptyStateChips<C extends { action: { kind: string } }>(chips: C[]): C[];
export function afterBill<R>(state: SheetState, provider: Store, request: R): { state: SheetState; emit: { kind: "bill"; provider: Store; stage: "bill_ready"; request: R } };
export function projectMomentum(velocity: number, decelerationRate?: number): number;
export function shouldDismiss(input: { offset: number; velocity: number; height: number }): boolean;
