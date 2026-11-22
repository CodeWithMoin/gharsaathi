"use client";

import { motion } from "framer-motion";
import { CheckCircle, CircleNotch, MapPin, Money, WarningCircle } from "@phosphor-icons/react";
import { useCallback, useEffect, useState } from "react";
import { ApiError, useApiRequest } from "./authenticated-api";
import { BASKET_CHANGED_EVENT } from "./basket/BasketPanel";
import {
  countdownLabel,
  deliverToLabel,
  orderCardResultText,
  orderCardStateFromResponse,
  type OrderCardState,
  type OrderConfirmation,
} from "./order-confirmation.mjs";

/**
 * The only control that places an order prepared by voice or typed chat. The
 * tap approves exactly this bill and places the Zepto cash-on-delivery order.
 */
export function OrderConfirmationCard({ confirmation, onResult }: { confirmation: OrderConfirmation; onResult?: (state: OrderCardState) => void }) {
  const apiRequest = useApiRequest();
  const [state, setState] = useState<OrderCardState>({ status: "open" });
  const [now, setNow] = useState(() => Date.now());
  const countdown = countdownLabel(confirmation.expiresAt, now);
  const open = state.status === "open";

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
    setState({ status: "placing" });
    try {
      const body = await apiRequest<{ status: string; provider_order_id: string | null }>("/api/orders/confirm", {
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
  const busy = state.status === "placing" || state.status === "cancelling";

  return <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="mt-8 mr-8 min-w-0 sm:mr-12">
    <section aria-label={`Confirm ${confirmation.store} order`} className="max-w-[620px] rounded-[8px_22px_22px_22px] border border-[#e8cbb9] bg-[#fffdf9] p-4 shadow-[0_18px_45px_-34px_rgba(74,43,27,.7)] sm:p-5">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold text-[#3b2a22]">{confirmation.store} order · {confirmation.itemCount} {confirmation.itemCount === 1 ? "item" : "items"}</h3>
        {open && countdown && <span className="shrink-0 text-[11px] font-medium tabular-nums text-[#89766a]" aria-live="off">{countdown}</span>}
      </div>
      <ul className="mt-3 space-y-1 text-sm text-[#5f4a3e]">
        {confirmation.items.map((item, index) => <li key={`${item.name}-${index}`} className="flex gap-2"><span className="shrink-0 tabular-nums text-[#89766a]">{item.quantity} ×</span><span className="min-w-0">{item.name}{item.packSize ? ` · ${item.packSize}` : ""}</span></li>)}
      </ul>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[#75645a]">
        <span className="flex items-center gap-1.5"><Money size={14} /> Cash on delivery</span>
        {deliverTo && <span className="flex items-center gap-1.5"><MapPin size={14} /> {deliverTo}</span>}
      </div>
      <p className="mt-3 text-base font-semibold text-[#3b2a22]">Total {confirmation.payable}</p>
      {result ? (
        <p role="status" className={`mt-4 flex items-start gap-2 rounded-2xl px-3.5 py-3 text-sm ${state.status === "placed" ? "bg-[#eff8f0] text-[#2d6638]" : "bg-[#fbf1ec] text-[#8a3f2a]"}`}>
          {state.status === "placed" ? <CheckCircle size={18} weight="fill" className="mt-0.5 shrink-0" /> : <WarningCircle size={18} className="mt-0.5 shrink-0" />}
          <span>{result}</span>
        </p>
      ) : (
        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <button type="button" onClick={() => void place()} disabled={busy} className="flex flex-1 items-center justify-center gap-2 rounded-full bg-[#9b3d17] px-5 py-3 text-sm font-semibold text-white shadow-[0_10px_24px_-14px_rgba(119,41,15,.8)] transition hover:bg-[#833212] active:scale-[.98] disabled:cursor-not-allowed disabled:opacity-45">
            {state.status === "placing" ? <><CircleNotch size={17} className="animate-spin" /> Placing order…</> : `Place order · ${confirmation.payable}`}
          </button>
          <button type="button" onClick={() => void cancel()} disabled={busy} className="rounded-full border border-[#deccbf] px-5 py-3 text-sm font-semibold text-[#5f4a3e] transition hover:bg-[#f6ece4] active:scale-[.98] disabled:cursor-not-allowed disabled:opacity-45">Cancel</button>
        </div>
      )}
      {open && <p className="mt-2 text-center text-[10px] leading-4 text-[#89766a]">Tapping Place order approves this exact bill and places the order. It can&apos;t be undone here.</p>}
    </section>
  </motion.div>;
}
