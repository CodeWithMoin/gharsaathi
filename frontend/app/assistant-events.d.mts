import type { ApprovalRequest, ProviderName } from "./basket/BasketPanel";
import type { AddressPrefill } from "./voice-address-prefill.mjs";
import type { OrderConfirmation } from "./order-confirmation.mjs";

export type AssistantAddressFormRequest = { prefill: AddressPrefill } | { unavailable: string };

export type AssistantEventHandlers<Product> = {
  onResults?: (query: string, products: Product[], item: { itemKey?: string; itemLabel?: string }) => void;
  onBasketTouched?: (provider?: ProviderName) => void;
  onApprovalRequested?: (provider: ProviderName, request: ApprovalRequest) => void;
  onAddressForm?: (request: AssistantAddressFormRequest) => void;
  onOrderConfirmation?: (confirmation: OrderConfirmation) => void;
};

export type AssistantEventOutcome = {
  kind: "results" | "not_connected" | "error" | "basket" | "bill" | "address_form" | "order_confirmation";
  message: string;
  notice?: string;
};

export function applyAssistantEvent<Product = unknown>(raw: unknown, handlers: AssistantEventHandlers<Product>): AssistantEventOutcome | null;
