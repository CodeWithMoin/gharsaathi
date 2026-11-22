export type OrderItem = { name: string; packSize: string | null; quantity: number };
export type PaymentMethod = "cod" | "upi_reserve" | "wallet" | "online";
export type PaymentOption = { id: PaymentMethod; label: string };

export type OrderConfirmation = {
  id: string;
  provider: "swiggy_instamart" | "zepto";
  store: string;
  expiresAt: string;
  payable: string;
  payablePaise: number;
  itemCount: number;
  items: OrderItem[];
  addressLabel: string | null;
  address: string | null;
  paymentMethod: PaymentMethod;
  paymentMethods: PaymentOption[];
};

export type OrderCardState =
  | { status: "open" }
  | { status: "placing" }
  | { status: "placed"; orderId: string; tracking?: unknown }
  | { status: "unknown" }
  | { status: "failed"; reason: string }
  | { status: "cancelling" }
  | { status: "cancelled" }
  | { status: "expired" }
  | { status: "switching" }
  | { status: "awaiting_payment"; checkoutId: string; url: string | null }
  | { status: "checking"; checkoutId: string; url: string | null }
  | { status: "payment_succeeded"; checkoutId: string }
  | { status: "payment_unknown"; checkoutId: string | null }
  | { status: "payment_failed" }
  | { status: "paid_order_failed" };

export const PAYMENT_METHOD_LABELS: Record<PaymentMethod, string>;
export function safePaymentUrl(raw: unknown): string | null;
export function withPaymentMethodCard(confirmation: OrderConfirmation, body: unknown): OrderConfirmation | null;
export function placeButtonLabel(confirmation: OrderConfirmation): string;
export function paymentMethodLabel(confirmation: OrderConfirmation): string;
export function checkableCheckoutId(state: OrderCardState): string | null;
export function autoCheckDelayMs(checksSoFar: number): number | null;

export function orderConfirmationFromEvent(raw: unknown): OrderConfirmation | null;
export function countdownLabel(expiresAt: string, nowMs: number): string | null;
export function deliverToLabel(confirmation: OrderConfirmation): string | null;
export function orderCardResultText(state: OrderCardState, store: string): string | null;
export function orderCardStateFromResponse(
  outcome: { ok: true; body: unknown } | { ok: false; status: number; message: string },
): OrderCardState;
export function orderCardIndex(items: { kind: string; message?: { id: string } }[], anchorId: string | null): number;
