import type { ApprovalRequest, ProviderName } from "./basket/BasketPanel";
import type { AddressPrefill } from "./voice-address-prefill.mjs";
import type { OrderConfirmation } from "./order-confirmation.mjs";
import type { AddressFormAck } from "./address-form-merge.mjs";

export type AssistantAddressFormRequest = { prefill: AddressPrefill } | { unavailable: string };

export type AssistantEventHandlers<Product> = {
  onResults?: (query: string, products: Product[], item: { itemKey?: string; itemLabel?: string }) => void;
  onBasketTouched?: (provider?: ProviderName) => void;
  onApprovalRequested?: (provider: ProviderName, request: ApprovalRequest) => void;
  /** For a prefill request, resolve with what the screen shows; the result is reported to the assistant. */
  onAddressForm?: (request: AssistantAddressFormRequest) => void | Promise<{ form_visible: boolean; reason?: string; filled_fields?: string[]; kept_user_fields?: string[] }>;
  onAddressSelected?: (selection: { provider: ProviderName; addressId: string; label?: string; billCancelled: boolean }) => void;
  onOrderConfirmation?: (confirmation: OrderConfirmation) => void;
};

export type AssistantEventOutcome = {
  kind: "results" | "not_connected" | "error" | "basket" | "bill" | "address_form" | "address" | "order_confirmation";
  message: string;
  notice?: string;
  /** address_form.open only: what the screen showed, with the event's ack_id when it had one. */
  ack?: Promise<AddressFormAck & { ack_id?: string }>;
  ackId?: string;
};

export function applyAssistantEvent<Product = unknown>(raw: unknown, handlers: AssistantEventHandlers<Product>): AssistantEventOutcome | null;
