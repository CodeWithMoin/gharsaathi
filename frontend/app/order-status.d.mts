export type CardStatus = "unknown" | "confirmed" | "preparing" | "out_for_delivery" | "delivered" | "cancelled" | "paused" | "unavailable";

export type OrderStatusCard = {
  provider: "zepto";
  orderId: string;
  monitorId: string | null;
  store: string;
  afterMessageId: string | null;
  seq: number;
  itemCount: number | null;
  payable: string | null;
  status: CardStatus;
  providerStatus: string | null;
  etaAt: string | null;
  checkedAt: string | null;
  active: boolean;
  lastNotificationId: string | null;
  itemNames: string[];
  finishedAt: string | null;
  placedAt: string | null;
  tookMinutes: number | null;
  estimateMinutes: number | null;
};

export type OrderStep = { id: string; label: string; state: "done" | "current" | "todo" | "cancelled" };

export type OrderCardSnapshot = {
  provider: "zepto";
  order_id: string;
  monitor_id: string | null;
  store: string;
  after_message_id: string | null;
  seq: number;
  item_count: number | null;
  payable: string | null;
  status: CardStatus;
  provider_status: string | null;
  eta_at: string | null;
  checked_at: string | null;
  active: boolean;
  last_notification_id: string | null;
  item_names: string[];
  finished_at: string | null;
  placed_at: string | null;
  took_minutes: number | null;
  estimate_minutes: number | null;
};

export const ORDER_STEPS: readonly ["confirmed", "preparing", "out_for_delivery", "delivered"];
export const CARD_STATUSES: readonly CardStatus[];
export const MAX_ORDER_CARDS: number;
export const MIN_POLL_MS: number;
export const MAX_POLL_MS: number;
export function orderSteps(status: CardStatus): OrderStep[];
export function orderHeadline(card: Pick<OrderStatusCard, "status" | "providerStatus">): string;
export function isFinal(card: OrderStatusCard): boolean;
export function orderCardFromPlaced(input: { orderId: string; store: string; afterMessageId: string | null; seq: number; itemCount?: number | null; payable?: string | null; itemNames?: string[]; tracking?: unknown }): OrderStatusCard;
export function finishedOrderSummary(card: OrderStatusCard): string | null;
export function applyTrackingRead(card: OrderStatusCard, raw: unknown): { card: OrderStatusCard; changed: boolean; toast: string | null };
export function pollDelayMs(raw: unknown, nowMs: number): number | null;
export function etaText(card: Pick<OrderStatusCard, "etaAt" | "status" | "store">, nowMs: number): string | null;
export function placedText(card: Pick<OrderStatusCard, "placedAt">): string | null;
export function deliveredText(card: Pick<OrderStatusCard, "status" | "finishedAt" | "tookMinutes" | "estimateMinutes">): string | null;
export function updatedText(checkedAt: string | null, nowMs: number): string | null;
export function orderCardSnapshot(card: OrderStatusCard): OrderCardSnapshot;
export function restoreOrderCard(raw: unknown): OrderStatusCard | null;
