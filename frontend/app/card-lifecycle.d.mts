import type { BasketSummary } from "./chat-timeline.mjs";
import type { OrderCardState, OrderConfirmation } from "./order-confirmation.mjs";

export type Store = "swiggy_instamart" | "zepto";
export type BasketLike = { id: string; provider: Store; revision: number; status: string; lines: { quantity: number }[] };
export type BillLike = { basket: { id: string; revision: number } };
export type LiveOrderCard = {
  confirmation: OrderConfirmation;
  afterMessageId: string | null;
  result: OrderCardState["status"] | null;
  superseded: boolean;
};

export const STALE_BILL_TEXT: string;
export const STALE_ORDER_TEXT: string;
export function billIsStale(basket: Pick<BasketLike, "id" | "revision"> | null | undefined, bill: BillLike | null | undefined): boolean;
export function newOrderCard(confirmation: OrderConfirmation, afterMessageId: string | null): LiveOrderCard;
export function orderCardKey(card: LiveOrderCard): string;
export function withOrderResult(card: LiveOrderCard | null, confirmationId: string, result: OrderCardState["status"]): LiveOrderCard | null;
export function supersedeOrderCard(card: LiveOrderCard | null, basket: BasketLike): LiveOrderCard | null;
export function basketFromEvent(raw: unknown, provider: Store | undefined): BasketLike | null;
export function orderCardInteractive(card: LiveOrderCard): boolean;
export function supersededOrderText(card: LiveOrderCard): string;
export function cartBadgeCount(baskets: Partial<Record<Store, BasketLike | undefined>>): number;
export function orderedSummary(order: { itemCount: number; payablePaise: number; orderId: string }): BasketSummary;
