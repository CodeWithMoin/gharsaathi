"use client";

import { motion } from "framer-motion";
import { ArrowSquareOut, CheckCircle, CircleNotch, MapPin, Money, WarningCircle } from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, useApiRequest } from "./authenticated-api";
import { BASKET_CHANGED_EVENT } from "./basket/BasketPanel";
import {
  autoCheckDelayMs,
  checkableCheckoutId,
  checkableOrderId,
  countdownLabel,
  deliverToLabel,
  orderCardResultText,
  orderCardStateFromResponse,
  orderCheckDelayMs,
  paymentMethodLabel,
  placeButtonLabel,
  withPaymentMethodCard,
  type OrderCardState,
  type OrderConfirmation,
  type PaymentMethod,
} from "./order-confirmation.mjs";

type OrderResponse = { status: string; provider_order_id: string | null; provider_order_code?: string | null; checkout_id?: string | null; check?: string | null; candidates?: string[]; payment?: { url?: string | null } };

/**
 * The only control that places an order prepared by voice or typed chat. The
 * tap approves exactly this bill and places the Zepto order with the chosen
 * payment method. Prepaid methods are paid only in the user's UPI app or on
 * Zepto's own page; this card never asks for a PIN, card number or OTP.
 */
export function OrderConfirmationCard({ confirmation: initial, onResult }: { confirmation: OrderConfirmation; onResult?: (state: OrderCardState) => void }) {
  const apiRequest = useApiRequest();
  const [confirmation, setConfirmation] = useState(initial);
  const [state, setState] = useState<OrderCardState>({ status: "open" });
  const [notice, setNotice] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const checks = useRef(0);
  const orderChecks = useRef(0);
  const countdown = countdownLabel(confirmation.expiresAt, now);
  const open = state.status === "open";
  const prepaid = confirmation.paymentMethod !== "cod";
  const checkoutId = checkableCheckoutId(state);
  const unknownCheckoutId = checkableOrderId(state);

  useEffect(() => {
    if (!open) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [open]);

  const finish = useCallback((next: OrderCardState) => {
    setState(next);
    onResult?.(next);
  }, [onResult]);

  useEffect(() => {
    if (open && countdown === null) finish({ status: "expired" });
  }, [countdown, finish, open]);

  const place = useCallback(async () => {
    if (state.status !== "open") return;
    setNotice(null);
    setState({ status: "placing" });
    try {
      const body = await apiRequest<OrderResponse>("/api/orders/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation_id: confirmation.id }),
      });
      finish(orderCardStateFromResponse({ ok: true, body }));
    } catch (cause) {
      finish(orderCardStateFromResponse(cause instanceof ApiError ? { ok: false, status: cause.status, message: cause.message } : { ok: false, status: 0, message: "" }));
    } finally {
      window.dispatchEvent(new Event(BASKET_CHANGED_EVENT));
    }
  }, [apiRequest, confirmation.id, finish, state.status]);

  const choose = useCallback(async (method: PaymentMethod) => {
    if (state.status !== "open" || method === confirmation.paymentMethod) return;
    setNotice(null);
    setState({ status: "switching" });
    try {
      const body = await apiRequest<unknown>("/api/orders/payment-method", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation_id: confirmation.id, payment_method: method }),
      });
      const next = withPaymentMethodCard(confirmation, body);
      if (next) setConfirmation(next);
      else setNotice("Couldn't change the payment method. Ask for the order again.");
    } catch (cause) {
      setNotice(cause instanceof ApiError ? cause.message : "Couldn't change the payment method. Try again.");
    }
    setState({ status: "open" });
  }, [apiRequest, confirmation, state.status]);

  const checkStatus = useCallback(async () => {
    const id = checkableCheckoutId(state);
    if (!id) return;
    const previous = state;
    checks.current += 1;
    setNotice(null);
    setState({ status: "checking", checkoutId: id, url: "url" in previous ? previous.url : null });
    try {
      const body = await apiRequest<OrderResponse>("/api/orders/payment-status", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ checkout_id: id }),
      });
      finish(orderCardStateFromResponse({ ok: true, body }));
    } catch (cause) {
      // A failed read changes nothing: keep the current payment state.
      setState(previous);
      setNotice(cause instanceof ApiError ? cause.message : "Couldn't check the payment. Try again in a moment.");
    } finally {
      window.dispatchEvent(new Event(BASKET_CHANGED_EVENT));
    }
  }, [apiRequest, finish, state]);

  // "Check with Zepto": one read-only order-history check; never another order call.
  const checkOrder = useCallback(async () => {
    const id = checkableOrderId(state);
    if (!id) return;
    const previous = state;
    orderChecks.current += 1;
    setNotice(null);
    setState({ status: "checking_order", checkoutId: id });
    try {
      const body = await apiRequest<OrderResponse>("/api/orders/check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ checkout_id: id }),
      });
      const next = orderCardStateFromResponse({ ok: true, body });
      finish(next.status === "unknown" && !next.checkoutId ? { ...next, checkoutId: id } : next);
    } catch (cause) {
      setState(previous);
      setNotice(cause instanceof ApiError ? cause.message : `Couldn't reach ${confirmation.store}. Try again in a moment.`);
    } finally {
      window.dispatchEvent(new Event(BASKET_CHANGED_EVENT));
    }
  }, [apiRequest, confirmation.store, finish, state]);

  // After an unknown outcome: three automatic checks over about two minutes, then only on tap.
  useEffect(() => {
    if (!unknownCheckoutId) return;
    const delay = orderCheckDelayMs(orderChecks.current);
    if (delay === null) return;
    const timer = window.setTimeout(() => void checkOrder(), delay);
    return () => window.clearTimeout(timer);
  }, [checkOrder, unknownCheckoutId]);

  // While waiting for payment: one status check when the user comes back to the tab, and a slow schedule.
  useEffect(() => {
    if (!checkoutId) return;
    const onVisible = () => { if (document.visibilityState === "visible") void checkStatus(); };
    document.addEventListener("visibilitychange", onVisible);
    const delay = autoCheckDelayMs(checks.current);
    const timer = delay === null ? undefined : window.setTimeout(() => void checkStatus(), delay);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [checkStatus, checkoutId]);

  const cancel = useCallback(async () => {
    if (state.status !== "open") return;
    setState({ status: "cancelling" });
    try {
      await apiRequest("/api/orders/cancel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation_id: confirmation.id }),
      });
    } catch {
      // Already used, expired or replaced: the card cannot place an order either way.
    }
    finish({ status: "cancelled" });
  }, [apiRequest, confirmation.id, finish, state.status]);

  const result = orderCardResultText(state, confirmation.store);
  const deliverTo = deliverToLabel(confirmation);
  const busy = state.status === "placing" || state.status === "cancelling" || state.status === "switching";
  const paymentUrl = state.status === "awaiting_payment" || state.status === "checking" ? state.url : null;
  const good = state.status === "placed";
  const unconfirmed = state.status === "unknown" || state.status === "checking_order";
  const waiting = state.status === "awaiting_payment" || state.status === "checking" || state.status === "payment_succeeded" || state.status === "payment_unknown";
  const chips = open || state.status === "switching";

  return <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="mt-8 mr-8 min-w-0 sm:mr-12">
    <section aria-label={`Confirm ${confirmation.store} order`} className="max-w-[620px] rounded-[8px_22px_22px_22px] border border-accent-border bg-surface p-4 shadow-[0_18px_45px_-34px_rgb(var(--shadow)/.7)] sm:p-5">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold text-ink">{confirmation.store} order · {confirmation.itemCount} {confirmation.itemCount === 1 ? "item" : "items"}</h3>
        {open && countdown && <span className="shrink-0 text-[11px] font-medium tabular-nums text-muted" aria-live="off">{countdown}</span>}
      </div>
      <ul className="mt-3 space-y-1 text-sm text-ink-secondary">
        {confirmation.items.map((item, index) => <li key={`${item.name}-${index}`} className="flex gap-2"><span className="shrink-0 tabular-nums text-muted">{item.quantity} ×</span><span className="min-w-0">{item.name}{item.packSize ? ` · ${item.packSize}` : ""}</span></li>)}
      </ul>
      {chips && confirmation.paymentMethods.length > 1 && (
        <div role="radiogroup" aria-label="Payment method" className="mt-3 flex flex-wrap gap-2">
          {confirmation.paymentMethods.map((option) => {
            const selected = option.id === confirmation.paymentMethod;
            return <button key={option.id} type="button" role="radio" aria-checked={selected} disabled={busy} onClick={() => void choose(option.id)} className={`rounded-full border px-3.5 py-1.5 text-xs font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:cursor-not-allowed disabled:opacity-55 ${selected ? "border-accent-border bg-accent-soft text-accent-strong" : "border-border-strong bg-surface text-ink-secondary hover:bg-surface-muted"}`}>{option.label}</button>;
          })}
        </div>
      )}
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        <span className="flex items-center gap-1.5"><Money size={14} /> {paymentMethodLabel(confirmation)}</span>
        {deliverTo && <span className="flex items-center gap-1.5"><MapPin size={14} /> {deliverTo}</span>}
      </div>
      <p className="mt-3 text-base font-semibold text-ink">Total {confirmation.payable}</p>
      {result ? (
        <>
          <p role="status" className={`mt-4 flex items-start gap-2 rounded-2xl px-3.5 py-3 text-sm ${good ? "bg-success-soft text-success-strong" : waiting || unconfirmed ? "bg-warning-soft text-warning" : "bg-surface-muted text-danger-hover"}`}>
            {good ? <CheckCircle size={18} weight="fill" className="mt-0.5 shrink-0" /> : <WarningCircle size={18} className="mt-0.5 shrink-0" />}
            <span>{result}</span>
          </p>
          {unconfirmed && (unknownCheckoutId || state.status === "checking_order") && (
            <button type="button" onClick={() => void checkOrder()} disabled={state.status === "checking_order"} className="mt-3 flex w-full items-center justify-center gap-2 rounded-full border border-border-strong px-5 py-3 text-sm font-semibold text-ink-secondary transition hover:bg-surface-muted active:scale-[.98] disabled:cursor-not-allowed disabled:opacity-55">
              {state.status === "checking_order" ? <><CircleNotch size={17} className="animate-spin" /> Checking with {confirmation.store}…</> : `Check with ${confirmation.store}`}
            </button>
          )}
          {waiting && (
            <div className="mt-3 flex flex-col gap-2 sm:flex-row">
              {paymentUrl && <a href={paymentUrl} target={paymentUrl.startsWith("https:") ? "_blank" : undefined} rel="noopener noreferrer" className="flex flex-1 items-center justify-center gap-2 rounded-full bg-accent-strong px-5 py-3 text-sm font-semibold text-on-accent transition hover:bg-accent-hover active:scale-[.98]"><ArrowSquareOut size={16} /> Open payment</a>}
              {checkoutId || state.status === "checking" ? (
                <button type="button" onClick={() => void checkStatus()} disabled={state.status === "checking"} className="flex flex-1 items-center justify-center gap-2 rounded-full border border-border-strong px-5 py-3 text-sm font-semibold text-ink-secondary transition hover:bg-surface-muted active:scale-[.98] disabled:cursor-not-allowed disabled:opacity-55">
                  {state.status === "checking" ? <><CircleNotch size={17} className="animate-spin" /> Checking…</> : "I've paid — check status"}
                </button>
              ) : null}
            </div>
          )}
        </>
      ) : (
        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <button type="button" onClick={() => void place()} disabled={busy} className="flex flex-1 items-center justify-center gap-2 rounded-full bg-accent-strong px-5 py-3 text-sm font-semibold text-on-accent shadow-[0_10px_24px_-14px_rgb(var(--accent-shadow)/.8)] transition hover:bg-accent-hover active:scale-[.98] disabled:cursor-not-allowed disabled:opacity-45">
            {state.status === "placing" ? <><CircleNotch size={17} className="animate-spin" /> {prepaid ? "Asking Zepto…" : "Placing order…"}</> : placeButtonLabel(confirmation)}
          </button>
          <button type="button" onClick={() => void cancel()} disabled={busy} className="rounded-full border border-border-strong px-5 py-3 text-sm font-semibold text-ink-secondary transition hover:bg-surface-muted active:scale-[.98] disabled:cursor-not-allowed disabled:opacity-45">Cancel</button>
        </div>
      )}
      {notice && <p role="alert" className="mt-2 text-xs text-danger">{notice}</p>}
      {(open || state.status === "switching") && (
        <p className="mt-2 text-center text-[10px] leading-4 text-muted">
          {prepaid
            ? "Tapping Pay approves this exact bill and asks Zepto for the payment. You pay only in your UPI app or on Zepto's page. GharSaathi never asks for your UPI PIN, card number or OTP."
            : <>Tapping Place order approves this exact bill and places the order. It can&apos;t be undone here.</>}
        </p>
      )}
    </section>
  </motion.div>;
}
