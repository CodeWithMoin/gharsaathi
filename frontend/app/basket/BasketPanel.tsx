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
    ? "border-[#cce2d0] bg-[#eff8f0] text-[#2d6638]"
    : unknown
      ? "border-[#f0d8c5] bg-[#fff0df] text-[#8c5127]"
      : checkout.status === "rejected"
        ? "border-[#efcfc7] bg-[#fcedea] text-[#974237]"
        : "border-[#e8cbb9] bg-[#fff5ee] text-[#80533c]";

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
    setRequest(incomingRequest);
    setApproval(incomingRequest.approval);
    onApprovalChange?.(incomingRequest.approval);
    setNotice(null);
  }, [applyBasket, incomingRequest, onApprovalChange]);

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
      const path = `/lines/${encodeURIComponent(line.ref.variant_id)}${query}`;
      const next = quantity <= 0
        ? await basketRequest<ShoppingBasket>(path, { method: "DELETE" })
        : await basketRequest<ShoppingBasket>(path, { method: "PATCH", body: JSON.stringify({ quantity }) });
      applyBasket(next);
      clearQuote();
      setNotice(quantity <= 0 ? { text: `${line.name} removed from your basket.`, error: false } : null);
    } catch (cause) {
      setNotice({ text: cause instanceof Error ? cause.message : "Could not update the quantity.", error: true });
    } finally {
      setAction(null);
    }
  }, [applyBasket, basketRequest, clearQuote, query]);

  const requestQuote = useCallback(async () => {
    setAction("quote");
    setNotice(null);
    try {
      const next = await basketRequest<ApprovalRequest>(`/approval${query}`, { method: "POST" });
      onBillStep?.(provider, "bill_ready");
      applyBasket(next.basket);
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

  const actionableFreeDelivery = request?.quote.offers?.find((offer) =>
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
  const visibleOffers = request ? visibleOfferOpportunities(request.quote.offers ?? []) as OfferOpportunity[] : [];

  return (
    <motion.section initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className={`overflow-hidden rounded-[26px] border border-[#e6d7cb] bg-[#fffdf9] shadow-[0_18px_45px_-34px_rgba(74,43,27,.7)] ${className}`} aria-label="Your basket" aria-busy={locked}>
      <header className="flex items-center gap-3 border-b border-[#efe4dc] px-5 py-4 sm:px-6">
        <span className="grid size-10 place-items-center rounded-[14px] bg-[#f6e9df] text-[#c6531d]"><Basket size={20} weight="fill" /></span>
        <div className="min-w-0">
          <p className="text-[10px] font-semibold uppercase tracking-[.16em] text-[#b07659]">Your basket</p>
          <h2 className="font-display text-lg font-semibold tracking-[-.025em] text-[#35241c]">{lineCount ? `${lineCount} ${lineCount === 1 ? "item" : "items"} ready to review` : "Add the things you need"}</h2>
        </div>
        {action === "load" && <CircleNotch size={18} className="ml-auto animate-spin text-[#b17d60]" aria-label="Loading basket" />}
      </header>

      {action === "load" && !basket ? (
        <div className="space-y-3 px-5 py-5 sm:px-6">
          <div className="h-16 animate-pulse rounded-2xl bg-[#f4ebe5]" />
          <div className="h-16 animate-pulse rounded-2xl bg-[#f4ebe5]" />
        </div>
      ) : lines.length === 0 ? (
        <div className="px-5 py-8 text-center sm:px-6">
          <span className="mx-auto grid size-12 place-items-center rounded-2xl bg-[#f8eee7] text-[#bd7851]"><Receipt size={24} /></span>
          <p className="mt-3 text-sm font-semibold text-[#453128]">Basket is empty</p>
          <p className="mx-auto mt-1 max-w-sm text-sm leading-6 text-[#806f65]">Add a product from the options above. You&apos;ll see the exact bill before you approve anything.</p>
        </div>
      ) : (
        <div className="px-5 py-2 sm:px-6">
          {basketFrozen && <div className="mt-4 flex gap-2 rounded-xl border border-[#f0d8c5] bg-[#fff0df] px-3 py-2.5 text-xs leading-5 text-[#8c5127]" role="status"><WarningCircle size={16} className="mt-0.5 shrink-0" weight="fill" /><p>{basket?.status === "ordered" ? "This basket was ordered and is locked." : "This checkout may already have reached Zepto. The basket is locked—do not recreate or submit it again. Check Zepto order history if the result is unclear."}</p></div>}
          <ul className="divide-y divide-[#f0e5dd]">
            {lines.map((line) => {
              const updating = action === `line-${line.ref.variant_id}`;
              return (
                <li key={line.ref.variant_id} className={`grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 py-4 ${line.available ? "" : "opacity-60"}`}>
                  <div className="min-w-0">
                    {line.brand && <p className="truncate text-[9px] font-semibold uppercase tracking-[.12em] text-[#bd7047]">{line.brand}</p>}
                    <h3 className="mt-0.5 line-clamp-2 text-sm font-semibold leading-5 text-[#3b2a22]">{line.name}</h3>
                    <p className="mt-0.5 truncate text-xs text-[#89766a]">{[line.pack_size, `${line.unit_price.display} each`].filter(Boolean).join(" · ")}</p>
                    {!line.available && <p className="mt-1 text-[11px] font-medium text-[#a44739]">No longer available</p>}
                    <div className="mt-3 flex items-center gap-2">
                      <div className="flex h-8 items-center rounded-full border border-[#e6d7cb] bg-white" role="group" aria-label={`Quantity of ${line.name}`}>
                        <button type="button" onClick={() => void changeQuantity(line, line.quantity - 1)} disabled={locked || basketFrozen} className="grid size-8 place-items-center rounded-full text-[#725a4b] transition hover:bg-[#f6e9df] disabled:opacity-40" aria-label={`Decrease quantity of ${line.name}`}><Minus size={13} weight="bold" /></button>
                        <span className="min-w-6 text-center text-xs font-semibold tabular-nums" aria-live="polite">{updating ? <CircleNotch size={13} className="mx-auto animate-spin" /> : line.quantity}</span>
                        <button type="button" onClick={() => void changeQuantity(line, line.quantity + 1)} disabled={locked || basketFrozen || line.quantity >= MAX_QUANTITY || !line.available} className="grid size-8 place-items-center rounded-full text-[#725a4b] transition hover:bg-[#f6e9df] disabled:opacity-40" aria-label={`Increase quantity of ${line.name}`}><Plus size={13} weight="bold" /></button>
                      </div>
                      <button type="button" onClick={() => void changeQuantity(line, 0)} disabled={locked || basketFrozen} className="grid size-8 place-items-center rounded-full text-[#9d7764] transition hover:bg-[#f9e9e3] hover:text-[#a44739] disabled:opacity-40" aria-label={`Remove ${line.name}`}><Trash size={15} /></button>
                    </div>
                  </div>
                  <div className="flex flex-col items-end text-right">
                    <strong className="text-sm tabular-nums text-[#3b2a22]">{line.line_total.display}</strong>
                    {line.savings.paise > 0 && <span className="mt-1 rounded-full bg-[#e8f2e8] px-2 py-0.5 text-[9px] font-semibold text-[#397144]">Save {line.savings.display}</span>}
                  </div>
                </li>
              );
            })}
          </ul>

          {!request && (
            <div className="border-t border-[#efe4dc] py-4">
              <div className="flex items-baseline justify-between gap-3 text-sm"><span className="text-[#806f65]">Items total</span><strong className="tabular-nums text-[#3b2a22]">{basket?.item_total.display}</strong></div>
              {basket && basket.savings_vs_mrp.paise > 0 && <div className="mt-1 flex items-baseline justify-between gap-3 text-xs"><span className="text-[#806f65]">Savings vs MRP</span><strong className="tabular-nums text-[#397144]">{basket.savings_vs_mrp.display}</strong></div>}
              {!addressId && <p className="mt-3 rounded-xl bg-[#f8eee7] px-3 py-2.5 text-xs leading-5 text-[#806f65]">Choose a delivery address to request the final bill.</p>}
              {addressId && <div className="mt-3 flex gap-2 rounded-xl border border-[#f0d8c5] bg-[#fff4e9] px-3 py-2.5 text-[11px] leading-5 text-[#80533c]" role="note"><WarningCircle size={16} className="mt-0.5 shrink-0 text-[#c6531d]" /><p>{commerceMode === "demo" ? <><strong className="font-semibold text-[#6a3d29]">Safe demo:</strong> this calculates a deterministic sample bill locally. It does not contact {providerLabel}, change a provider cart, check out, or place an order.</> : <><strong className="font-semibold text-[#6a3d29]">Before you continue:</strong> this syncs this basket to your current {providerLabel} cart to retrieve live availability, fees, and the exact bill. It may replace items already in that provider cart. It does <strong className="font-semibold">not</strong> check out or place an order.</>}</p></div>}
              <button type="button" onClick={() => void requestQuote()} disabled={locked || basketFrozen || !addressId} className="mt-4 flex w-full items-center justify-center gap-2 rounded-full bg-[#3b2a22] px-5 py-3 text-sm font-semibold text-white shadow-[0_12px_30px_-18px_rgba(53,36,28,.8)] transition hover:bg-[#4a352b] active:scale-[.98] disabled:cursor-not-allowed disabled:opacity-45">
                {action === "quote" ? <><CircleNotch size={17} className="animate-spin" /> Getting final bill…</> : <><Receipt size={17} /> Get final bill</>}
              </button>
            </div>
          )}
        </div>
      )}

      <AnimatePresence initial={false}>
        {request && approval && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden border-t border-[#ead8ca] bg-[#fff8f2]">
            <div className="p-5 sm:p-6">
              <div className="flex items-start gap-3">
                <span className="grid size-10 shrink-0 place-items-center rounded-[14px] bg-[#f5e2d5] text-[#c6531d]"><ShieldCheck size={21} weight="fill" /></span>
                <div className="min-w-0"><p className="text-[10px] font-semibold uppercase tracking-[.16em] text-[#b07659]">Final bill · approval needed</p><h3 className="mt-0.5 text-base font-semibold text-[#35241c]">Review this exact bill</h3><p className="mt-1 text-xs leading-5 text-[#806f65]">Approval is bound to this exact basket, price, and expiry. Any material change needs a fresh approval.</p></div>
              </div>

              <ul className="mt-4 space-y-2 border-y border-[#eaded5] py-3 text-xs leading-5">
                {request.basket.lines.map((line) => <li key={line.ref.variant_id} className="flex justify-between gap-4"><span className="min-w-0 text-[#69564b]"><b className="font-semibold text-[#47332a]">{line.quantity}×</b> {line.name}</span><span className="shrink-0 tabular-nums text-[#47332a]">{line.line_total.display}</span></li>)}
                <li className="flex justify-between gap-4 pt-2 text-[#806f65]"><span>Store item total</span><span className="tabular-nums">{request.quote.item_total.display}</span></li>
                {request.quote.charges.map((charge, index) => <li key={`${charge.label}-${index}`} className="flex justify-between gap-4 text-[#806f65]"><span>{charge.label}</span><span className="tabular-nums">{charge.amount.display}</span></li>)}
                <li className="flex justify-between gap-4 pt-2 text-sm font-semibold text-[#35241c]"><span>Final payable</span><strong className="tabular-nums">{request.quote.payable.display}</strong></li>
              </ul>

              {visibleOffers.length > 0 && (
                <div className="mt-3 rounded-2xl border border-[#ded3ef] bg-[#f8f4ff] p-3.5" aria-label="Smart savings suggestions">
                  <div className="flex items-start gap-2">
                    <span className="grid size-8 shrink-0 place-items-center rounded-xl bg-[#ebe1fa] text-[#6f359f]"><Receipt size={16} weight="fill" /></span>
                    <div>
                      <p className="text-xs font-semibold text-[#4d2b69]">Smart savings — only if it helps</p>
                      <p className="mt-0.5 text-[10px] leading-4 text-[#766381]">We won&apos;t add anything automatically. Any change gets a fresh live bill and needs approval again.</p>
                    </div>
                  </div>
                  <ul className="mt-3 space-y-2">
                    {visibleOffers.map((offer, index) => {
                      const copy = offerOpportunityCopy(offer);
                      return <li key={`${offer.kind}-${offer.title}-${index}`} className="rounded-xl bg-white/80 px-3 py-2.5"><p className="text-xs font-semibold text-[#493354]">{copy.title}</p><p className="mt-0.5 text-[10px] leading-4 text-[#766381]">{copy.detail}</p></li>;
                    })}
                  </ul>
                  {actionableFreeDelivery && provider === "zepto" && (
                    <div className="mt-3 border-t border-[#e5dcef] pt-3">
                      {usuals === null ? <button type="button" onClick={() => void loadUsefulUsuals()} disabled={locked} className="rounded-full border border-[#cdb8df] bg-white px-3.5 py-2 text-[11px] font-semibold text-[#65358b] disabled:opacity-45">{action === "offer-usuals" ? "Checking…" : "Check a useful usual item"}</button> : usuals.length > 0 && <div><p className="text-[10px] leading-4 text-[#766381]">You bought these before. That does not mean you need them—tap one only if it&apos;s useful now.</p><div className="mt-2 flex flex-wrap gap-2">{usuals.map((usual) => <button type="button" key={usual.product_id} onClick={() => void reviewUsefulUsual(usual)} disabled={locked} className="rounded-full border border-[#d8c9e5] bg-white px-3 py-1.5 text-[10px] font-semibold text-[#5f4173] disabled:opacity-45">{action === `offer-usual-${usual.product_id}` ? "Checking live price…" : usual.product_name}</button>)}</div></div>}
                      {usualCandidate && <div className="mt-3 rounded-xl border border-[#d5c4e3] bg-white p-3"><p className="text-xs font-semibold text-[#493354]">{usualCandidate.name} · {usualCandidate.price.display} live</p><p className="mt-1 text-[10px] leading-4 text-[#766381]">This item unlocks the threshold. After the confirmed {usualCandidate.confirmedSaving.display} delivery saving, {usualCandidate.netIncrementalCost.paise < 0 ? `your bill could be ${usualCandidate.netIncrementalCost.display} lower` : `the net extra is ${usualCandidate.netIncrementalCost.display}`}.</p><button type="button" onClick={() => void addUsefulUsual()} disabled={locked} className="mt-2 rounded-full bg-[#6f359f] px-3.5 py-2 text-[11px] font-semibold text-white disabled:opacity-45">{action === "offer-add" ? "Adding…" : `Add ${usualCandidate.name}`}</button><p className="mt-1.5 text-[9px] leading-4 text-[#83718d]">Adding it cancels this approval. Zepto must return a fresh bill before you can approve again.</p></div>}
                      {usualNotice && <p className="mt-2 text-[10px] leading-4 text-[#805d46]">{usualNotice}</p>}
                    </div>
                  )}
                </div>
              )}

              {!request.item_total_matches && <p className="mt-3 flex gap-2 rounded-xl bg-[#fff0df] px-3 py-2.5 text-xs leading-5 text-[#8c5127]"><WarningCircle size={16} className="mt-0.5 shrink-0" />Store item prices changed. The final bill above is the amount you would approve.</p>}
              <p className="mt-3 text-[11px] leading-5 text-[#806f65]">{request.quote.eta_text ? `Delivery ${request.quote.eta_text} · ` : ""}Valid {expiryText(approval.expires_at)}.</p>

              {isApproved ? <div className="mt-4 space-y-2"><div className="flex items-center gap-2 rounded-2xl border border-[#cce2d0] bg-[#eff8f0] px-3.5 py-3 text-sm text-[#2d6638]"><CheckCircle size={20} weight="fill" /><span><strong>Bill approved.</strong> {canConfirmZeptoCod ? "Review the final COD action below." : "No order has been placed."}</span></div>{canConfirmZeptoCod ? (
                checkout ? <ZeptoCheckoutOutcome checkout={checkout.result} /> : <div className="space-y-2 rounded-2xl border border-[#e8cbb9] bg-[#fff5ee] p-3.5"><p className="text-sm font-semibold text-[#5f3421]">One final step: place a Zepto cash-on-delivery order</p><p className="text-xs leading-5 text-[#80533c]">This submits the exact approved bill to Zepto. It cannot be undone here.</p><button type="button" onClick={() => void confirmZeptoCodOrder()} disabled={locked} className="flex w-full items-center justify-center gap-2 rounded-full bg-[#9b3d17] px-5 py-3 text-sm font-semibold text-white shadow-[0_10px_24px_-14px_rgba(119,41,15,.8)] transition hover:bg-[#833212] active:scale-[.98] disabled:cursor-not-allowed disabled:opacity-45">{action === "checkout" ? <><CircleNotch size={17} className="animate-spin" /> Submitting COD order…</> : `Place Zepto COD order · ${request.quote.payable.display}`}</button></div>
              ) : <div className="flex gap-2 rounded-xl bg-[#fff0df] px-3 py-2.5 text-[11px] leading-5 text-[#8c5127]" role="note"><WarningCircle size={16} className="mt-0.5 shrink-0" /><p>A separate, explicit order confirmation would still be required before anything can be submitted to {providerLabel}. That final action is not enabled here.</p></div>}</div> : <div className="mt-4 grid grid-cols-2 gap-2"><button type="button" onClick={() => void reject()} disabled={locked} className="rounded-full border border-[#d8c4b7] bg-white px-4 py-3 text-sm font-semibold text-[#684f42] transition hover:bg-[#fbf1eb] active:scale-[.98] disabled:opacity-45">{action === "reject" ? <CircleNotch className="mx-auto animate-spin" size={17} /> : "Reject"}</button><button type="button" onClick={() => void approve()} disabled={locked} className="rounded-full bg-[#c6531d] px-4 py-3 text-sm font-semibold text-white shadow-[0_10px_24px_-14px_rgba(158,65,24,.8)] transition hover:bg-[#b94a18] active:scale-[.98] disabled:opacity-45">{action === "approve" ? <CircleNotch className="mx-auto animate-spin" size={17} /> : `Approve bill · ${request.quote.payable.display}`}</button></div>}
              {!isApproved && <p className="mt-3 text-center text-[10px] leading-4 text-[#89766a]">This approves only the bill shown above—it doesn&apos;t check out. Placing the order needs a separate final confirmation.</p>}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence initial={false}>
        {notice && <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }} className={`mx-5 mb-5 flex items-start gap-2 rounded-xl px-3 py-2.5 text-xs leading-5 sm:mx-6 sm:mb-6 ${notice.error ? "bg-[#fcedea] text-[#974237]" : "bg-[#edf6ee] text-[#397144]"}`} role="status" aria-live="polite"><span className="mt-0.5">{notice.error ? <WarningCircle size={15} weight="fill" /> : <CheckCircle size={15} weight="fill" />}</span><span>{notice.text}</span><button type="button" onClick={() => setNotice(null)} className="ml-auto -mr-1 shrink-0 rounded-full p-0.5 opacity-70 transition hover:opacity-100" aria-label="Dismiss message"><X size={14} /></button></motion.div>}
      </AnimatePresence>
    </motion.section>
  );
}

export default BasketPanel;
