export type OrderItem = { name: string; packSize: string | null; quantity: number };

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
};

export type OrderCardState =
  | { status: "open" }
  | { status: "placing" }
  | { status: "placed"; orderId: string; tracking?: unknown }
  | { status: "unknown" }
  | { status: "failed"; reason: string }
  | { status: "cancelling" }
  | { status: "cancelled" }
  | { status: "expired" };

export function orderConfirmationFromEvent(raw: unknown): OrderConfirmation | null;
export function countdownLabel(expiresAt: string, nowMs: number): string | null;
export function deliverToLabel(confirmation: OrderConfirmation): string | null;
export function orderCardResultText(state: OrderCardState, store: string): string | null;
export function orderCardStateFromResponse(
  outcome: { ok: true; body: unknown } | { ok: false; status: number; message: string },
): OrderCardState;
export function orderCardIndex(items: { kind: string; message?: { id: string } }[], anchorId: string | null): number;
