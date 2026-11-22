import { sanitizeAddressPrefill } from "./voice-address-prefill.mjs";
import { voiceApprovalFromToolEvent } from "./voice-approval.mjs";
import { orderConfirmationFromEvent } from "./order-confirmation.mjs";

const PROVIDERS = new Set(["swiggy_instamart", "zepto"]);

const ADDRESS_UNAVAILABLE_MESSAGES = {
  zepto_not_connected: "Connect Zepto first to add a new address.",
  demo_mode: "New addresses can't be added in demo mode.",
  swiggy_unsupported: "Add Swiggy addresses in the Swiggy app.",
};

/**
 * Applies one assistant tool event, from the voice sideband or from a typed
 * `/api/chat/turn` response, through the page's handlers. Events are
 * untrusted: malformed ones are ignored. Returns the status to show, or null.
 *
 * @param {unknown} raw
 * @param {{
 *   onResults?: (query: string, products: any[], item: { itemKey?: string, itemLabel?: string }) => void,
 *   onBasketTouched?: (provider?: "swiggy_instamart" | "zepto") => void,
 *   onApprovalRequested?: (provider: "swiggy_instamart" | "zepto", request: any) => void,
 *   onAddressForm?: (request: { prefill: Record<string, string> } | { unavailable: string }) => void,
 *   onOrderConfirmation?: (confirmation: import("./order-confirmation.mjs").OrderConfirmation) => void,
 * }} handlers
 */
export function applyAssistantEvent(raw, handlers) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const event = /** @type {Record<string, any>} */ (raw);
  const provider = PROVIDERS.has(event.provider) ? event.provider : undefined;

  if (event.type === "tool.results") {
    if (!Array.isArray(event.products)) return null;
    handlers.onResults?.(typeof event.query === "string" ? event.query : "", event.products, {
      itemKey: typeof event.item_key === "string" && event.item_key ? event.item_key : undefined,
      itemLabel: typeof event.item_label === "string" && event.item_label ? event.item_label : undefined,
    });
    return { kind: "results", message: "Found options. Showing the best choices." };
  }
  if (event.type === "tool.not_connected") {
    return { kind: "not_connected", message: "Connect a store and choose a delivery address." };
  }
  if (event.type === "tool.error") {
    return { kind: "error", message: typeof event.message === "string" && event.message ? event.message : "Something went wrong. Please try again." };
  }
  if (event.type === "basket.updated") {
    const basket = event.basket && typeof event.basket === "object" ? event.basket : {};
    const items = Number.isInteger(basket.item_count) ? basket.item_count : 0;
    const store = typeof basket.store === "string" && basket.store ? `${basket.store} · ` : "";
    const total = typeof basket.item_total === "string" ? basket.item_total : "";
    handlers.onBasketTouched?.(provider);
    return {
      kind: "basket",
      message: items ? "Basket updated." : "Basket is now empty.",
      notice: `${store}${items} ${items === 1 ? "item" : "items"}${total ? ` · ${total}` : ""}`,
    };
  }
  if (event.type === "basket.approval_requested") {
    const approval = voiceApprovalFromToolEvent(event);
    if (approval) handlers.onApprovalRequested?.(approval.provider, approval.request);
    handlers.onBasketTouched?.(provider);
    const payable = typeof event.approval?.payable === "string" ? event.approval.payable : "";
    return {
      kind: "bill",
      message: "Final bill is ready—review it on screen.",
      notice: `${typeof event.store === "string" && event.store ? `${event.store} · ` : ""}Final bill${payable ? ` · ${payable}` : ""}`,
    };
  }
  if (event.type === "order.confirmation_requested") {
    const confirmation = orderConfirmationFromEvent(event);
    if (!confirmation) return null;
    // A freshly requested bill travels with the card, in the basket.approval_requested shape.
    const approval = voiceApprovalFromToolEvent({ ...event, type: "basket.approval_requested" });
    if (approval) handlers.onApprovalRequested?.(approval.provider, approval.request);
    handlers.onOrderConfirmation?.(confirmation);
    return {
      kind: "order_confirmation",
      message: "Your order is ready—tap Place order to confirm.",
      notice: `${confirmation.store} · Place order · ${confirmation.payable}`,
    };
  }
  if (event.type === "address_form.open" && event.provider === "zepto") {
    handlers.onAddressForm?.({ prefill: sanitizeAddressPrefill(event.prefill) });
    return { kind: "address_form", message: "The address form is open—check the details and tap Save." };
  }
  if (event.type === "address_form.unavailable" && typeof event.reason === "string") {
    handlers.onAddressForm?.({ unavailable: event.reason });
    return { kind: "address_form", message: ADDRESS_UNAVAILABLE_MESSAGES[event.reason] ?? "A new address can't be added right now." };
  }
  return null;
}
