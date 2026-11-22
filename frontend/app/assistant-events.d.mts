import type { ApprovalRequest, ProviderName } from "./basket/BasketPanel";
import type { AddressPrefill } from "./voice-address-prefill.mjs";
import type { OrderConfirmation } from "./order-confirmation.mjs";
import type { AddressFormAck } from "./address-form-merge.mjs";
import type { BasketLike } from "./card-lifecycle.mjs";
import type { Comparison } from "./comparison-card.mjs";

export type AssistantAddressFormRequest = { prefill: AddressPrefill } | { unavailable: string };

export type AssistantEventHandlers<Product> = {
  onResults?: (query: string, products: Product[], item: { itemKey?: string; itemLabel?: string }) => void;
  /** `basket` is the store's basket from a `basket.updated` event, when it carried one. */
  onBasketTouched?: (provider?: ProviderName, basket?: BasketLike) => void;
  onApprovalRequested?: (provider: ProviderName, request: ApprovalRequest) => void;
  /** For a prefill request, resolve with what the screen shows; the result is reported to the assistant. */
  onAddressForm?: (request: AssistantAddressFormRequest) => void | Promise<{ form_visible: boolean; reason?: string; filled_fields?: string[]; kept_user_fields?: string[] }>;
  onAddressSelected?: (selection: { provider: ProviderName; addressId: string; label?: string; billCancelled: boolean }) => void;
  onOrderConfirmation?: (confirmation: OrderConfirmation) => void;
  /** `support.open`: show the Order help popup. */
  onSupportOpen?: () => void;
  /** The store's selected saved address was deleted there; open its address picker. */
  onAddressRemoved?: (provider: ProviderName) => void;
  /** `photo.read`: the server read the attached photo and deleted it. */
  onPhotoRead?: (read: { attachmentId: string; itemCount: number }) => void;
  /** `memory.summary`: open Settings → What GharSaathi knows. */
  onMemorySummary?: () => void;
  /** `comparison.ready`: show (or replace) the one live comparison card. */
  onComparison?: (comparison: Comparison) => void;
};

export const ADDRESS_REMOVED_MESSAGE: string;

export type AssistantEventOutcome = {
  kind: "results" | "not_connected" | "error" | "basket" | "bill" | "address_form" | "address" | "order_confirmation" | "support" | "photo" | "memory" | "comparison";
  message: string;
  notice?: string;
  /** address_form.open only: what the screen showed, with the event's ack_id when it had one. */
  ack?: Promise<AddressFormAck & { ack_id?: string }>;
  ackId?: string;
};

export function applyAssistantEvent<Product = unknown>(raw: unknown, handlers: AssistantEventHandlers<Product>): AssistantEventOutcome | null;
