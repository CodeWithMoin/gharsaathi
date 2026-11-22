"use client";

import {
  Basket,
  CheckCircle,
  CircleNotch,
  Minus,
  Plus,
  Receipt,
  ShieldCheck,
  Trash,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { AnimatePresence, motion } from "framer-motion";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, useApiRequest } from "../authenticated-api";
import { offerOpportunityCopy, visibleOfferOpportunities } from "./offer-opportunity.mjs";
import { canConfirmZeptoCodOrder, checkoutStatusCopy } from "./zepto-checkout-state.mjs";
import { STALE_BILL_TEXT, billIsStale } from "../card-lifecycle.mjs";

const MAX_QUANTITY = 50;

export const BASKET_CHANGED_EVENT = "gharsaathi:basket-changed";

export type ProviderName = "swiggy_instamart" | "zepto";
export type Money = { paise: number; display: string };

export type BasketLine = {
  ref: { provider: ProviderName; product_id: string; variant_id: string; sku_id?: string | null };
  name: string;
  brand?: string | null;
  pack_size?: string | null;
  quantity: number;
  available: boolean;
  unit_price: Money;
  mrp?: Money | null;
  line_total: Money;
  savings: Money;
};

export type ShoppingBasket = {
  id: string;
  provider: ProviderName;
  address_id?: string | null;
  status: "draft" | "awaiting_approval" | "approved" | "checkout_pending" | "ordered" | "abandoned";
  revision: number;
  lines: BasketLine[];
  item_total: Money;
  savings_vs_mrp: Money;
};

export type Approval = {
  id: string;
  status: "pending" | "approved" | "rejected" | "expired" | "superseded" | "consuming" | "consumed";
  fingerprint: string;
  payable: Money;
  payment_method?: string | null;
  expires_at: string;
};

export type ApprovalRequest = {
  approval: Approval;
  quote: {
    item_total: Money;
    charges: { label: string; amount: Money }[];
    payable: Money;
    eta_text?: string | null;
    offers?: OfferOpportunity[];
  };
  basket: ShoppingBasket;
  item_total_matches: boolean;
};

export type OfferOpportunity = {
  kind: "free_delivery" | "bank_offer" | "promo" | "other";
  title: string;
  minimum_order_paise: number;
  remaining_paise: number;
  potential_saving_paise: number | null;
  net_extra_paise: number | null;
  unlocked: boolean;
  applied: boolean;
  provider_confirmed: boolean;
};

type UsualSuggestion = {
  product_id: string;
  product_name: string;
  provider: string;
  provider_item_id?: string | null;
  last_ordered_at: string;
};

type LiveUsualCandidate = {
  line: AddBasketLine;
  name: string;
  price: Money;
  confirmedSaving: Money;
  netIncrementalCost: Money;
};

type OfferSuggestionResponse = {
  line: {
    ref: BasketLine["ref"];
    name: string;
    brand?: string | null;
    pack_size?: string | null;
    unit_price_paise: number;
    mrp_paise?: number | null;
    quantity: number;
    available: boolean;
  };
  candidate_total_paise: number;
  confirmed_benefit_paise: number;
  net_incremental_cost_paise: number;
  requires_explicit_add: true;
  requires_fresh_quote: true;
};

type ZeptoCheckoutStatus = "dispatching" | "confirmed" | "outcome_unknown" | "rejected";

type ZeptoCheckoutResult = {
  status: ZeptoCheckoutStatus;
  provider_order_id?: string | null;
  message?: string | null;
};

type ZeptoCheckoutState = {
  approvalId: string;
  fingerprint: string;
  idempotencyKey: string;
  result: ZeptoCheckoutResult;
};

export type AddBasketLine = {
  ref: BasketLine["ref"];
  name: string;
  brand?: string | null;
  pack_size?: string | null;
  unit_price_paise: number;
  mrp_paise?: number | null;
  quantity?: number;
  available?: boolean;
};

type BasketPanelProps = {
  provider?: ProviderName;
  addressId?: string;
  commerceMode?: "demo" | "live";
  /** A quote produced by voice can be passed in here. It still needs a deliberate tap. */
  incomingRequest?: ApprovalRequest | null;
  onBasketChange?: (basket: ShoppingBasket) => void;
  onApprovalChange?: (approval: Approval | null) => void;
  /** Called before a new bill or approval is shown, so the chat can move this card to now. */
  onBillStep?: (provider: ProviderName, stage: "bill_ready" | "bill_approved") => void;
  className?: string;
};

function providerQuery(provider: ProviderName) {
  return `?provider=${encodeURIComponent(provider)}`;
}

/**
 * Lets a product card add an item without knowing about this component's UI.
 * The panel listens for BASKET_CHANGED_EVENT and refreshes itself afterwards.
 */
export function useAddBasketLine() {
  const request = useApiRequest();
  return useCallback(async (provider: ProviderName, line: AddBasketLine): Promise<ShoppingBasket> => {
    const basket = await request<ShoppingBasket>(`/api/basket/lines${providerQuery(provider)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(line),
    });
    window.dispatchEvent(new Event(BASKET_CHANGED_EVENT));
    return basket;
  }, [request]);
}

/**
 * Sets one basket line's quantity; 0 removes the line. Shared by the basket
 * card and the product option cards.
 */
export function useSetBasketLineQuantity() {
  const request = useApiRequest();
  return useCallback(async (provider: ProviderName, variantId: string, quantity: number): Promise<ShoppingBasket> => {
    const path = `/api/basket/lines/${encodeURIComponent(variantId)}${providerQuery(provider)}`;
    return quantity <= 0
      ? request<ShoppingBasket>(path, { method: "DELETE" })
      : request<ShoppingBasket>(path, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ quantity }) });
  }, [request]);
}

function expiryText(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "for a limited time";
  return `until ${date.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" })}`;
}

function ZeptoCheckoutOutcome({ checkout }: { checkout: ZeptoCheckoutResult }) {
  const submitting = checkout.status === "dispatching";
  const succeeded = checkout.status === "confirmed";
  const unknown = checkout.status === "outcome_unknown";
  const className = succeeded
    ? "border-success-border bg-success-soft text-success-strong"
    : unknown
      ? "border-warning-border bg-warning-soft text-warning"
      : checkout.status === "rejected"
        ? "border-danger-border bg-danger-soft text-danger-hover"
        : "border-accent-border bg-warning-soft text-warning";

  return (
    <div className={`rounded-2xl border px-3.5 py-3 text-sm leading-5 ${className}`} role="status" aria-live="polite">
      <div className="flex items-start gap-2">
        {submitting ? <CircleNotch size={20} className="mt-0.5 shrink-0 animate-spin" aria-label="Submitting order" /> : succeeded ? <CheckCircle size={20} weight="fill" className="mt-0.5 shrink-0" /> : <WarningCircle size={20} weight="fill" className="mt-0.5 shrink-0" />}
        <div>
          <p className="font-semibold">{submitting ? "Submitting Zepto COD order" : succeeded ? "Zepto COD order placed" : unknown ? "Order status is unknown" : "Zepto did not accept the order"}</p>
          <p className="mt-0.5 text-xs leading-5">{checkoutStatusCopy(checkout)}</p>
          {succeeded && checkout.provider_order_id && <p className="mt-2 text-xs font-semibold">Use this order ID in Zepto order history: {checkout.provider_order_id}</p>}
          {unknown && <p className="mt-2 text-xs font-semibold">Do not retry this action. Check Zepto or your order history first.</p>}
        </div>
      </div>
    </div>
  );
}

export function BasketPanel({
  provider = "swiggy_instamart",
  addressId = "",
  commerceMode = "live",
  incomingRequest = null,
  onBasketChange,
  onApprovalChange,
  onBillStep,
  className = "",
}: BasketPanelProps) {
  const apiRequest = useApiRequest();
  const setLineQuantity = useSetBasketLineQuantity();
  const basketRequest = useCallback(<T,>(path: string, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    if (init?.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    return apiRequest<T>(`/api/basket${path}`, { ...init, headers });
  }, [apiRequest]);
  const [basket, setBasket] = useState<ShoppingBasket | null>(null);
  const [request, setRequest] = useState<ApprovalRequest | null>(null);
  const [approval, setApproval] = useState<Approval | null>(null);
  const [action, setAction] = useState<string | null>("load");
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);
  const [checkout, setCheckout] = useState<ZeptoCheckoutState | null>(null);
  const [usuals, setUsuals] = useState<UsualSuggestion[] | null>(null);
  const [usualCandidate, setUsualCandidate] = useState<LiveUsualCandidate | null>(null);
  const [usualNotice, setUsualNotice] = useState<string | null>(null);
  // Set when the basket changed under a bill shown here: the old bill is gone, not reusable.
  const [billOutdated, setBillOutdated] = useState(false);
  const query = useMemo(() => providerQuery(provider), [provider]);

  const applyBasket = useCallback((next: ShoppingBasket) => {
    setBasket(next);
    onBasketChange?.(next);
  }, [onBasketChange]);

  const clearQuote = useCallback(() => {
    setRequest(null);
    setApproval(null);
    onApprovalChange?.(null);
  }, [onApprovalChange]);

  const load = useCallback(async (silent = false) => {
    if (!silent) setAction("load");
    try {
      const next = await basketRequest<ShoppingBasket>(query);
      applyBasket(next);
      if (!silent) setNotice(null);
    } catch (cause) {
      setNotice({ text: cause instanceof Error ? cause.message : "Could not load the basket.", error: true });
    } finally {
      if (!silent) setAction(null);
    }
  }, [applyBasket, basketRequest, query]);

  useEffect(() => {
    void load();
    const refresh = () => void load(true);
    window.addEventListener(BASKET_CHANGED_EVENT, refresh);
    return () => window.removeEventListener(BASKET_CHANGED_EVENT, refresh);
  }, [load]);

  useEffect(() => {
    if (!incomingRequest) return;
    applyBasket(incomingRequest.basket);
    setBillOutdated(false);
    setRequest(incomingRequest);
    setApproval(incomingRequest.approval);
    onApprovalChange?.(incomingRequest.approval);
    setNotice(null);
  }, [applyBasket, incomingRequest, onApprovalChange]);

  // A bill is bound to one basket revision. Once the basket changes (an edit here,
  // by voice or typed chat, or an order closing it), the bill is replaced at once.
  const billStale = !checkout && billIsStale(basket, request);
  useEffect(() => {
    if (!billStale) return;
    setRequest(null);
    setApproval(null);
    onApprovalChange?.(null);
    setBillOutdated(true);
  }, [billStale, onApprovalChange]);

  // A final-order result is valid only for the exact approved bill that created it.
  useEffect(() => {
    setCheckout(null);
    setUsuals(null);
    setUsualCandidate(null);
    setUsualNotice(null);
  }, [approval?.fingerprint, approval?.id, commerceMode, provider]);

  useEffect(() => {
    if (!basket || !addressId || basket.address_id === addressId) return;
    let active = true;
    setAction("address");
    void basketRequest<ShoppingBasket>(`/address${query}`, {
      method: "POST",
      body: JSON.stringify({ address_id: addressId }),
    }).then((next) => {
      if (!active) return;
      applyBasket(next);
      clearQuote();
      setNotice(null);
    }).catch((cause: unknown) => {
      if (!active) return;
      setNotice({ text: cause instanceof Error ? cause.message : "Could not set the delivery address.", error: true });
    }).finally(() => {
      if (active) setAction(null);
    });
    return () => { active = false; };
  }, [addressId, applyBasket, basket, basketRequest, clearQuote, query]);

  const changeQuantity = useCallback(async (line: BasketLine, quantity: number) => {
    setAction(`line-${line.ref.variant_id}`);
    try {
      const next = await setLineQuantity(provider, line.ref.variant_id, quantity);
      applyBasket(next);
      clearQuote();
      setNotice(quantity <= 0 ? { text: `${line.name} removed from your basket.`, error: false } : null);
    } catch (cause) {
      setNotice({ text: cause instanceof Error ? cause.message : "Could not update the quantity.", error: true });
    } finally {
      setAction(null);
    }
  }, [applyBasket, clearQuote, provider, setLineQuantity]);

  const requestQuote = useCallback(async () => {
    setAction("quote");
    setNotice(null);
    try {
      const next = await basketRequest<ApprovalRequest>(`/approval${query}`, { method: "POST" });
      onBillStep?.(provider, "bill_ready");
      applyBasket(next.basket);
      setBillOutdated(false);
      setRequest(next);
      setApproval(next.approval);
      onApprovalChange?.(next.approval);
    } catch (cause) {
      setNotice({ text: cause instanceof Error ? cause.message : "Could not get the final bill.", error: true });
    } finally {
      setAction(null);
    }
  }, [applyBasket, basketRequest, onApprovalChange, onBillStep, provider, query]);

  const approve = useCallback(async () => {
    if (!approval) return;
    setAction("approve");
    try {
      const next = await basketRequest<Approval>(`/approval/${encodeURIComponent(approval.id)}/approve`, {
        method: "POST",
        body: JSON.stringify({ fingerprint: approval.fingerprint }),
      });
      onBillStep?.(provider, "bill_approved");
      setApproval(next);
      onApprovalChange?.(next);
      setNotice({ text: "Approved. Your basket and final bill are locked for the next step.", error: false });
    } catch (cause) {
      setNotice({ text: cause instanceof Error ? cause.message : "Could not approve this bill.", error: true });
    } finally {
      setAction(null);
    }
  }, [approval, basketRequest, onApprovalChange, onBillStep, provider]);

  const reject = useCallback(async () => {
    if (!approval) return;
    setAction("reject");
    try {
      await basketRequest<Approval>(`/approval/${encodeURIComponent(approval.id)}/reject`, { method: "POST" });
      clearQuote();
      const next = await basketRequest<ShoppingBasket>(query);
      applyBasket(next);
      setNotice({ text: "Okay—nothing was ordered.", error: false });
    } catch (cause) {
      setNotice({ text: cause instanceof Error ? cause.message : "Could not reject this bill.", error: true });
    } finally {
      setAction(null);
    }
  }, [approval, applyBasket, basketRequest, clearQuote, query]);

  const confirmZeptoCodOrder = useCallback(async () => {
    if (!approval || !canConfirmZeptoCodOrder({ provider, commerceMode, approvalStatus: approval.status }) || checkout) return;

    // Keep this key with this exact approval, so a transport retry cannot become a second order.
    const idempotencyKey = crypto.randomUUID();
    const submitted: ZeptoCheckoutState = {
      approvalId: approval.id,
      fingerprint: approval.fingerprint,
      idempotencyKey,
      result: { status: "dispatching" },
    };
    setCheckout(submitted);
    setAction("checkout");
    setNotice(null);
    try {
      const result = await apiRequest<ZeptoCheckoutResult>("/api/zepto/checkout/cod", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify({ approval_id: approval.id, fingerprint: approval.fingerprint }),
      });
      setCheckout({ ...submitted, result });
    } catch (cause) {
      // A failed connection is an uncertain provider outcome, not permission to submit again.
      const detail = cause instanceof Error ? cause.message : "We could not confirm the Zepto order.";
      setCheckout({
        ...submitted,
        result: {
          status: cause instanceof ApiError && cause.status >= 400 && cause.status < 500
            ? "rejected"
            : "outcome_unknown",
          message: detail,
        },
      });
    } finally {
      setAction(null);
    }
  }, [apiRequest, approval, checkout, commerceMode, provider]);

  const shownRequest = billStale ? null : request;
  const actionableFreeDelivery = shownRequest?.quote.offers?.find((offer) =>
    offer.kind === "free_delivery"
    && !offer.unlocked
    && offer.remaining_paise > 0
    && offer.potential_saving_paise != null
    && offer.potential_saving_paise > 0
    && offer.provider_confirmed,
  ) ?? null;

  const loadUsefulUsuals = useCallback(async () => {
    if (!actionableFreeDelivery || provider !== "zepto") return;
    setAction("offer-usuals");
    setUsualCandidate(null);
    setUsualNotice(null);
    try {
      const suggestions = await apiRequest<UsualSuggestion[]>("/api/memory/usual-suggestions?limit=5");
      const zeptoUsuals = suggestions.filter((item) => item.provider === "zepto" && item.provider_item_id);
      setUsuals(zeptoUsuals);
      if (!zeptoUsuals.length) setUsualNotice("No consented Zepto usuals are ready to check. We won’t guess from unrelated history.");
    } catch (cause) {
      setUsualNotice(cause instanceof Error ? cause.message : "Could not load your usual items.");
    } finally {
      setAction(null);
    }
  }, [actionableFreeDelivery, apiRequest, provider]);

  const reviewUsefulUsual = useCallback(async (usual: UsualSuggestion) => {
    if (!actionableFreeDelivery || !addressId || !usual.provider_item_id) return;
    setAction(`offer-usual-${usual.product_id}`);
    setUsualCandidate(null);
    setUsualNotice(null);
    try {
      const suggestion = await apiRequest<OfferSuggestionResponse | null>("/api/zepto/offer-suggestions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          memory_product_id: usual.product_id,
          address_id: addressId,
          offer_id: "free_delivery",
          offer_label: actionableFreeDelivery.title,
          minimum_order_paise: actionableFreeDelivery.minimum_order_paise,
          remaining_paise: actionableFreeDelivery.remaining_paise,
          confirmed_benefit_paise: actionableFreeDelivery.potential_saving_paise,
        }),
      });
      if (!suggestion) {
        setUsualNotice(`${usual.product_name} is unavailable, stale, already in the basket, or would not unlock this offer by itself.`);
        return;
      }
      setUsualCandidate({
        name: suggestion.line.name,
        price: { paise: suggestion.candidate_total_paise, display: `₹${(suggestion.candidate_total_paise / 100).toLocaleString("en-IN")}` },
        confirmedSaving: { paise: suggestion.confirmed_benefit_paise, display: `₹${(suggestion.confirmed_benefit_paise / 100).toLocaleString("en-IN")}` },
        netIncrementalCost: { paise: suggestion.net_incremental_cost_paise, display: `₹${Math.abs(suggestion.net_incremental_cost_paise / 100).toLocaleString("en-IN")}` },
        line: {
          ref: suggestion.line.ref,
          name: suggestion.line.name,
          brand: suggestion.line.brand,
          pack_size: suggestion.line.pack_size,
          unit_price_paise: suggestion.line.unit_price_paise,
          mrp_paise: suggestion.line.mrp_paise,
          quantity: suggestion.line.quantity,
          available: suggestion.line.available,
        },
      });
    } catch (cause) {
      setUsualNotice(cause instanceof Error ? cause.message : "Could not check the current Zepto item.");
    } finally {
      setAction(null);
    }
  }, [actionableFreeDelivery, addressId, apiRequest]);

  const addUsefulUsual = useCallback(async () => {
    if (!usualCandidate) return;
    setAction("offer-add");
    try {
      const next = await basketRequest<ShoppingBasket>(`/lines${query}`, {
        method: "POST",
        body: JSON.stringify(usualCandidate.line),
      });
      applyBasket(next);
      clearQuote();
      setUsuals(null);
      setUsualCandidate(null);
      setUsualNotice(null);
      setNotice({ text: `${usualCandidate.name} added. Get a fresh live bill to confirm the offer and final payable.`, error: false });
    } catch (cause) {
      setUsualNotice(cause instanceof Error ? cause.message : "Could not add this item.");
    } finally {
      setAction(null);
    }
  }, [applyBasket, basketRequest, clearQuote, query, usualCandidate]);

  const lines = basket?.lines ?? [];
  const lineCount = lines.reduce((total, line) => total + line.quantity, 0);
  const locked = action !== null;
  const basketFrozen = basket?.status === "checkout_pending" || basket?.status === "ordered";
  const isApproved = approval?.status === "approved";
  const canConfirmZeptoCod = Boolean(approval && canConfirmZeptoCodOrder({ provider, commerceMode, approvalStatus: approval.status }));
  const providerLabel = provider === "swiggy_instamart" ? "Swiggy Instamart" : "Zepto";
  const visibleOffers = shownRequest ? visibleOfferOpportunities(shownRequest.quote.offers ?? []) as OfferOpportunity[] : [];

  return (
    <motion.section initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className={`overflow-hidden rounded-[26px] border border-border-strong bg-surface shadow-[0_18px_45px_-34px_rgb(var(--shadow)/.7)] ${className}`} aria-label="Your basket" aria-busy={locked}>
      <header className="flex items-center gap-3 border-b border-border px-5 py-4 sm:px-6">
        <span className="grid size-10 place-items-center rounded-[14px] bg-accent-soft text-accent-ink"><Basket size={20} weight="fill" /></span>
        <div className="min-w-0">
          <p className="text-[10px] font-semibold uppercase tracking-[.16em] text-accent-ink">Your basket</p>
          <h2 className="font-display text-lg font-semibold tracking-[-.025em] text-ink">{lineCount ? `${lineCount} ${lineCount === 1 ? "item" : "items"} ready to review` : "Add the things you need"}</h2>
        </div>
        {action === "load" && <CircleNotch size={18} className="ml-auto animate-spin text-accent-ink" aria-label="Loading basket" />}
      </header>

      {action === "load" && !basket ? (
        <div className="space-y-3 px-5 py-5 sm:px-6">
          <div className="h-16 animate-pulse rounded-2xl bg-surface-muted" />
          <div className="h-16 animate-pulse rounded-2xl bg-surface-muted" />
        </div>
      ) : lines.length === 0 ? (
        <div className="px-5 py-8 text-center sm:px-6">
          <span className="mx-auto grid size-12 place-items-center rounded-2xl bg-surface-muted text-accent-ink"><Receipt size={24} /></span>
          <p className="mt-3 text-sm font-semibold text-ink">Basket is empty</p>
          <p className="mx-auto mt-1 max-w-sm text-sm leading-6 text-muted">Add a product from the options above. You&apos;ll see the exact bill before you approve anything.</p>
        </div>
      ) : (
        <div className="px-5 py-2 sm:px-6">
          {basketFrozen && <div className="mt-4 flex gap-2 rounded-xl border border-warning-border bg-warning-soft px-3 py-2.5 text-xs leading-5 text-warning" role="status"><WarningCircle size={16} className="mt-0.5 shrink-0" weight="fill" /><p>{basket?.status === "ordered" ? "This basket was ordered and is locked." : "This checkout may already have reached Zepto. The basket is locked—do not recreate or submit it again. Check Zepto order history if the result is unclear."}</p></div>}
          <ul className="divide-y divide-border">
            {lines.map((line) => {
              const updating = action === `line-${line.ref.variant_id}`;
              return (
                <li key={line.ref.variant_id} className={`grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 py-4 ${line.available ? "" : "opacity-60"}`}>
                  <div className="min-w-0">
                    {line.brand && <p className="truncate text-[9px] font-semibold uppercase tracking-[.12em] text-accent-ink">{line.brand}</p>}
                    <h3 className="mt-0.5 line-clamp-2 text-sm font-semibold leading-5 text-ink">{line.name}</h3>
                    <p className="mt-0.5 truncate text-xs text-muted">{[line.pack_size, `${line.unit_price.display} each`].filter(Boolean).join(" · ")}</p>
                    {!line.available && <p className="mt-1 text-[11px] font-medium text-danger">No longer available</p>}
                    <div className="mt-3 flex items-center gap-2">
                      <div className="flex h-8 items-center rounded-full border border-border-strong bg-white" role="group" aria-label={`Quantity of ${line.name}`}>
                        <button type="button" onClick={() => void changeQuantity(line, line.quantity - 1)} disabled={locked || basketFrozen} className="grid size-8 place-items-center rounded-full text-ink-secondary transition hover:bg-accent-soft disabled:opacity-40" aria-label={`Decrease quantity of ${line.name}`}><Minus size={13} weight="bold" /></button>
                        <span className="min-w-6 text-center text-xs font-semibold tabular-nums" aria-live="polite">{updating ? <CircleNotch size={13} className="mx-auto animate-spin" /> : line.quantity}</span>
                        <button type="button" onClick={() => void changeQuantity(line, line.quantity + 1)} disabled={locked || basketFrozen || line.quantity >= MAX_QUANTITY || !line.available} className="grid size-8 place-items-center rounded-full text-ink-secondary transition hover:bg-accent-soft disabled:opacity-40" aria-label={`Increase quantity of ${line.name}`}><Plus size={13} weight="bold" /></button>
                      </div>
                      <button type="button" onClick={() => void changeQuantity(line, 0)} disabled={locked || basketFrozen} className="grid size-8 place-items-center rounded-full text-subtle transition hover:bg-danger-soft hover:text-danger disabled:opacity-40" aria-label={`Remove ${line.name}`}><Trash size={15} /></button>
                    </div>
                  </div>
                  <div className="flex flex-col items-end text-right">
                    <strong className="text-sm tabular-nums text-ink">{line.line_total.display}</strong>
                    {line.savings.paise > 0 && <span className="mt-1 rounded-full bg-success-soft px-2 py-0.5 text-[9px] font-semibold text-success">Save {line.savings.display}</span>}
                  </div>
                </li>
              );
            })}
          </ul>

          {!shownRequest && (
            <div className="border-t border-border py-4">
              {billOutdated && <p role="status" className="mb-3 flex gap-2 rounded-xl border border-warning-border bg-warning-soft px-3 py-2.5 text-xs font-semibold leading-5 text-warning"><WarningCircle size={16} className="mt-0.5 shrink-0" weight="fill" />{STALE_BILL_TEXT}</p>}
              <div className="flex items-baseline justify-between gap-3 text-sm"><span className="text-muted">Items total</span><strong className="tabular-nums text-ink">{basket?.item_total.display}</strong></div>
              {basket && basket.savings_vs_mrp.paise > 0 && <div className="mt-1 flex items-baseline justify-between gap-3 text-xs"><span className="text-muted">Savings vs MRP</span><strong className="tabular-nums text-success">{basket.savings_vs_mrp.display}</strong></div>}
              {!addressId && <p className="mt-3 rounded-xl bg-surface-muted px-3 py-2.5 text-xs leading-5 text-muted">Choose a delivery address to request the final bill.</p>}
              {addressId && <div className="mt-3 flex gap-2 rounded-xl border border-warning-border bg-warning-soft px-3 py-2.5 text-[11px] leading-5 text-warning" role="note"><WarningCircle size={16} className="mt-0.5 shrink-0 text-accent-ink" /><p>{commerceMode === "demo" ? <><strong className="font-semibold text-warning">Safe demo:</strong> this calculates a deterministic sample bill locally. It does not contact {providerLabel}, change a provider cart, check out, or place an order.</> : <><strong className="font-semibold text-warning">Before you continue:</strong> this syncs this basket to your current {providerLabel} cart to retrieve live availability, fees, and the exact bill. It may replace items already in that provider cart. It does <strong className="font-semibold">not</strong> check out or place an order.</>}</p></div>}
              <button type="button" onClick={() => void requestQuote()} disabled={locked || basketFrozen || !addressId} className="mt-4 flex w-full items-center justify-center gap-2 rounded-full bg-inverse px-5 py-3 text-sm font-semibold text-on-inverse shadow-[0_12px_30px_-18px_rgb(var(--shadow)/.8)] transition hover:bg-inverse-hover active:scale-[.98] disabled:cursor-not-allowed disabled:opacity-45">
                {action === "quote" ? <><CircleNotch size={17} className="animate-spin" /> Getting final bill…</> : <><Receipt size={17} /> Get final bill</>}
              </button>
            </div>
          )}
        </div>
      )}

      <AnimatePresence initial={false}>
        {shownRequest && approval && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden border-t border-warning-border bg-warning-soft">
            <div className="p-5 sm:p-6">
              <div className="flex items-start gap-3">
                <span className="grid size-10 shrink-0 place-items-center rounded-[14px] bg-accent-soft text-accent-ink"><ShieldCheck size={21} weight="fill" /></span>
                <div className="min-w-0"><p className="text-[10px] font-semibold uppercase tracking-[.16em] text-accent-ink">Final bill · approval needed</p><h3 className="mt-0.5 text-base font-semibold text-ink">Review this exact bill</h3><p className="mt-1 text-xs leading-5 text-muted">Approval is bound to this exact basket, price, and expiry. Any material change needs a fresh approval.</p></div>
              </div>

              <ul className="mt-4 space-y-2 border-y border-border py-3 text-xs leading-5">
                {shownRequest.basket.lines.map((line) => <li key={line.ref.variant_id} className="flex justify-between gap-4"><span className="min-w-0 text-ink-secondary"><b className="font-semibold text-ink">{line.quantity}×</b> {line.name}</span><span className="shrink-0 tabular-nums text-ink">{line.line_total.display}</span></li>)}
                <li className="flex justify-between gap-4 pt-2 text-muted"><span>Store item total</span><span className="tabular-nums">{shownRequest.quote.item_total.display}</span></li>
                {shownRequest.quote.charges.map((charge, index) => <li key={`${charge.label}-${index}`} className="flex justify-between gap-4 text-muted"><span>{charge.label}</span><span className="tabular-nums">{charge.amount.display}</span></li>)}
                <li className="flex justify-between gap-4 pt-2 text-sm font-semibold text-ink"><span>Final payable</span><strong className="tabular-nums">{shownRequest.quote.payable.display}</strong></li>
              </ul>

              {visibleOffers.length > 0 && (
                <div className="mt-3 rounded-2xl border border-zepto-border bg-zepto-soft p-3.5" aria-label="Smart savings suggestions">
                  <div className="flex items-start gap-2">
                    <span className="grid size-8 shrink-0 place-items-center rounded-xl bg-zepto-soft text-zepto-strong"><Receipt size={16} weight="fill" /></span>
                    <div>
                      <p className="text-xs font-semibold text-zepto-ink">Smart savings — only if it helps</p>
                      <p className="mt-0.5 text-[10px] leading-4 text-zepto-muted">We won&apos;t add anything automatically. Any change gets a fresh live bill and needs approval again.</p>
                    </div>
                  </div>
                  <ul className="mt-3 space-y-2">
                    {visibleOffers.map((offer, index) => {
                      const copy = offerOpportunityCopy(offer);
                      return <li key={`${offer.kind}-${offer.title}-${index}`} className="rounded-xl bg-white/80 px-3 py-2.5"><p className="text-xs font-semibold text-zepto-ink">{copy.title}</p><p className="mt-0.5 text-[10px] leading-4 text-zepto-muted">{copy.detail}</p></li>;
                    })}
                  </ul>
                  {actionableFreeDelivery && provider === "zepto" && (
                    <div className="mt-3 border-t border-zepto-border pt-3">
                      {usuals === null ? <button type="button" onClick={() => void loadUsefulUsuals()} disabled={locked} className="rounded-full border border-zepto-border bg-white px-3.5 py-2 text-[11px] font-semibold text-zepto-strong disabled:opacity-45">{action === "offer-usuals" ? "Checking…" : "Check a useful usual item"}</button> : usuals.length > 0 && <div><p className="text-[10px] leading-4 text-zepto-muted">You bought these before. That does not mean you need them—tap one only if it&apos;s useful now.</p><div className="mt-2 flex flex-wrap gap-2">{usuals.map((usual) => <button type="button" key={usual.product_id} onClick={() => void reviewUsefulUsual(usual)} disabled={locked} className="rounded-full border border-zepto-border bg-white px-3 py-1.5 text-[10px] font-semibold text-zepto-ink disabled:opacity-45">{action === `offer-usual-${usual.product_id}` ? "Checking live price…" : usual.product_name}</button>)}</div></div>}
                      {usualCandidate && <div className="mt-3 rounded-xl border border-zepto-border bg-white p-3"><p className="text-xs font-semibold text-zepto-ink">{usualCandidate.name} · {usualCandidate.price.display} live</p><p className="mt-1 text-[10px] leading-4 text-zepto-muted">This item unlocks the threshold. After the confirmed {usualCandidate.confirmedSaving.display} delivery saving, {usualCandidate.netIncrementalCost.paise < 0 ? `your bill could be ${usualCandidate.netIncrementalCost.display} lower` : `the net extra is ${usualCandidate.netIncrementalCost.display}`}.</p><button type="button" onClick={() => void addUsefulUsual()} disabled={locked} className="mt-2 rounded-full bg-zepto-strong px-3.5 py-2 text-[11px] font-semibold text-on-accent disabled:opacity-45">{action === "offer-add" ? "Adding…" : `Add ${usualCandidate.name}`}</button><p className="mt-1.5 text-[9px] leading-4 text-zepto-muted">Adding it cancels this approval. Zepto must return a fresh bill before you can approve again.</p></div>}
                      {usualNotice && <p className="mt-2 text-[10px] leading-4 text-ink-secondary">{usualNotice}</p>}
                    </div>
                  )}
                </div>
              )}

              {!shownRequest.item_total_matches && <p className="mt-3 flex gap-2 rounded-xl bg-warning-soft px-3 py-2.5 text-xs leading-5 text-warning"><WarningCircle size={16} className="mt-0.5 shrink-0" />Store item prices changed. The final bill above is the amount you would approve.</p>}
              <p className="mt-3 text-[11px] leading-5 text-muted">{shownRequest.quote.eta_text ? `Delivery ${shownRequest.quote.eta_text} · ` : ""}Valid {expiryText(approval.expires_at)}.</p>

              {isApproved ? <div className="mt-4 space-y-2"><div className="flex items-center gap-2 rounded-2xl border border-success-border bg-success-soft px-3.5 py-3 text-sm text-success-strong"><CheckCircle size={20} weight="fill" /><span><strong>Bill approved.</strong> {canConfirmZeptoCod ? "Review the final COD action below." : "No order has been placed."}</span></div>{canConfirmZeptoCod ? (
                checkout ? <ZeptoCheckoutOutcome checkout={checkout.result} /> : <div className="space-y-2 rounded-2xl border border-accent-border bg-accent-soft p-3.5"><p className="text-sm font-semibold text-ink-secondary">One final step: place a Zepto cash-on-delivery order</p><p className="text-xs leading-5 text-warning">This submits the exact approved bill to Zepto. It cannot be undone here.</p><button type="button" onClick={() => void confirmZeptoCodOrder()} disabled={locked} className="flex w-full items-center justify-center gap-2 rounded-full bg-accent-strong px-5 py-3 text-sm font-semibold text-on-accent shadow-[0_10px_24px_-14px_rgb(var(--accent-shadow)/.8)] transition hover:bg-accent-hover active:scale-[.98] disabled:cursor-not-allowed disabled:opacity-45">{action === "checkout" ? <><CircleNotch size={17} className="animate-spin" /> Submitting COD order…</> : `Place Zepto COD order · ${shownRequest.quote.payable.display}`}</button></div>
              ) : <div className="flex gap-2 rounded-xl bg-warning-soft px-3 py-2.5 text-[11px] leading-5 text-warning" role="note"><WarningCircle size={16} className="mt-0.5 shrink-0" /><p>A separate, explicit order confirmation would still be required before anything can be submitted to {providerLabel}. That final action is not enabled here.</p></div>}</div> : <div className="mt-4 grid grid-cols-2 gap-2"><button type="button" onClick={() => void reject()} disabled={locked} className="rounded-full border border-accent-border bg-white px-4 py-3 text-sm font-semibold text-ink-secondary transition hover:bg-surface-muted active:scale-[.98] disabled:opacity-45">{action === "reject" ? <CircleNotch className="mx-auto animate-spin" size={17} /> : "Reject"}</button><button type="button" onClick={() => void approve()} disabled={locked} className="rounded-full bg-accent px-4 py-3 text-sm font-semibold text-on-accent shadow-[0_10px_24px_-14px_rgb(var(--accent-shadow)/.8)] transition hover:bg-accent-hover active:scale-[.98] disabled:opacity-45">{action === "approve" ? <CircleNotch className="mx-auto animate-spin" size={17} /> : `Approve bill · ${shownRequest.quote.payable.display}`}</button></div>}
              {!isApproved && <p className="mt-3 text-center text-[10px] leading-4 text-muted">This approves only the bill shown above—it doesn&apos;t check out. Placing the order needs a separate final confirmation.</p>}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence initial={false}>
        {notice && <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }} className={`mx-5 mb-5 flex items-start gap-2 rounded-xl px-3 py-2.5 text-xs leading-5 sm:mx-6 sm:mb-6 ${notice.error ? "bg-danger-soft text-danger-hover" : "bg-success-soft text-success"}`} role="status" aria-live="polite"><span className="mt-0.5">{notice.error ? <WarningCircle size={15} weight="fill" /> : <CheckCircle size={15} weight="fill" />}</span><span>{notice.text}</span><button type="button" onClick={() => setNotice(null)} className="ml-auto -mr-1 shrink-0 rounded-full p-0.5 opacity-70 transition hover:opacity-100" aria-label="Dismiss message"><X size={14} /></button></motion.div>}
      </AnimatePresence>
    </motion.section>
  );
}

export default BasketPanel;
