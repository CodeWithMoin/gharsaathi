"use client";

import { motion } from "framer-motion";
import { CheckCircle, Circle, Clock, DeviceMobile, Lifebuoy, MapPin, NavigationArrow, Package, Phone, PhoneIncoming, Truck, XCircle } from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useApiRequest } from "./authenticated-api";
import { LiveOrderTracking } from "./order-tracking";
import { orderCardLinks, type OrderCardLink } from "./home-header.mjs";
import { applyTrackingRead, deliveredText, etaText, finishedOrderSummary, isFinal, orderHeadline, orderSteps, placedText, pollDelayMs, updatedText, type OrderStatusCard as Card } from "./order-status.mjs";
import { InputDialog } from "./ui/input-dialog";

/**
 * A placed order in the chat timeline, updated in place from the read-only
 * tracking monitor. It asks the backend only while the tab is visible and the
 * order is still moving, at the provider's allowed interval.
 */
export function OrderStatusCard({ card, onChange, onUpdate, riderLine = null, deliveryLineReady = false, onOpenDeliveryLine, onOpenDirections, onOpenSupport }: {
  card: Card;
  /** From a call to the inbound delivery line, e.g. "Rider has your directions". */
  riderLine?: string | null;
  /** The Sarvam delivery line is on for this server. */
  deliveryLineReady?: boolean;
  /** Opens Settings → Delivery line. */
  onOpenDeliveryLine?: () => void;
  /** Opens the directions editor for the selected address. */
  onOpenDirections?: () => void;
  /** Opens the existing Order help flow. */
  onOpenSupport?: () => void;
  /** Something worth saving with the chat changed (status, ETA, tracking stopped, a seen update). */
  onChange: (card: Card) => void;
  /** A new in-app update (out for delivery, delivered, delayed, cancelled) to show as a toast. */
  onUpdate: (message: string) => void;
}) {
  const apiRequest = useApiRequest();
  const [live, setLive] = useState(card);
  const [trackOpen, setTrackOpen] = useState(false);
  // A delivered or store-cancelled order collapses to one line; a tap shows the details.
  const [expanded, setExpanded] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const cardRef = useRef(card);
  const callbacks = useRef({ onChange, onUpdate });
  useEffect(() => { callbacks.current = { onChange, onUpdate }; });
  useEffect(() => { cardRef.current = { ...card, checkedAt: cardRef.current.checkedAt ?? card.checkedAt }; setLive(cardRef.current); }, [card]);

  const polling = Boolean(card.monitorId) && card.active && !isFinal(card);

  const refresh = useCallback(async (): Promise<number | null> => {
    const current = cardRef.current;
    if (!current.monitorId) return null;
    try {
      const read = await apiRequest<unknown>(`/api/tracking/monitors/${encodeURIComponent(current.monitorId)}/refresh`, { method: "POST", cache: "no-store" });
      const result = applyTrackingRead(current, read);
      cardRef.current = result.card;
      setLive(result.card);
      if (result.toast) callbacks.current.onUpdate(result.toast);
      if (result.changed) callbacks.current.onChange(result.card);
      return pollDelayMs(read, Date.now());
    } catch {
      // Keep the last store-reported status; try again later.
      return 60_000;
    }
  }, [apiRequest]);

  useEffect(() => {
    if (!polling) return;
    let timer: number | null = null;
    let cancelled = false;
    const schedule = (delay: number | null) => {
      if (cancelled || delay === null) return;
      timer = window.setTimeout(run, delay);
    };
    const run = async () => {
      timer = null;
      if (document.visibilityState !== "visible") return;
      schedule(await refresh());
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible" && timer === null) void run();
    };
    void run();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [polling, refresh]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const steps = orderSteps(live.status);
  const eta = etaText(live, now);
  const placed = placedText(live);
  const deliveredLine = deliveredText(live);
  const late = Boolean(eta?.startsWith("Running late"));
  const updated = updatedText(live.checkedAt, now);
  const cancelled = live.status === "cancelled";
  const delivered = live.status === "delivered";
  const stopped = !live.active && !isFinal(live);
  const itemLine = live.itemNames?.length ? live.itemNames.join(", ") : null;
  const summary = [live.itemCount !== null ? `${live.itemCount} ${live.itemCount === 1 ? "item" : "items"}` : null, live.payable, "Cash on delivery"].filter(Boolean).join(" · ");

  const finished = finishedOrderSummary(live);
  const links = orderCardLinks({ final: isFinal(live), deliveryLineReady }).filter((link) => link.id !== "help" || onOpenSupport).filter((link) => link.id !== "delivery-line" || onOpenDeliveryLine);
  const openLink = (link: OrderCardLink) => {
    if (link.id === "help") onOpenSupport?.();
    else if (link.id === "delivery-line") onOpenDeliveryLine?.();
    else if (onOpenDirections) onOpenDirections();
    else setTrackOpen(true);
  };
  if (finished && !expanded) {
    return <motion.div id={`order-status-${live.orderId}`} initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mt-6 mr-8 flex min-w-0 sm:mr-12">
      <button type="button" onClick={() => setExpanded(true)} aria-expanded={false} aria-label={`${finished}. Show order details`} className={`flex max-w-full items-center gap-2 truncate rounded-full px-3 py-1.5 text-[11px] font-medium transition hover:bg-surface-strong ${cancelled ? "bg-surface-strong text-danger-hover" : "bg-success-soft text-success-strong"}`}>
        {cancelled ? <XCircle size={13} weight="fill" className="shrink-0" /> : <CheckCircle size={13} weight="fill" className="shrink-0" />}
        <span className="truncate">{finished}</span>
      </button>
    </motion.div>;
  }

  return <motion.div id={`order-status-${live.orderId}`} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="mt-8 mr-8 min-w-0 sm:mr-12">
    <section aria-label={`${live.store} order status`} className="max-w-[620px] rounded-[8px_22px_22px_22px] border border-accent-border bg-surface p-4 shadow-[0_18px_45px_-34px_rgb(var(--shadow)/.7)] sm:p-5">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold text-ink">{live.store} order · #{live.orderId}</h3>
        {finished ? <button type="button" onClick={() => setExpanded(false)} className="shrink-0 text-[11px] font-semibold text-muted hover:text-ink">Collapse</button> : updated && <span className="shrink-0 text-[11px] font-medium text-muted">{updated}</span>}
      </div>
      {summary && <p className="mt-1 text-xs text-muted">{summary}</p>}
      {itemLine && finished && <p className="mt-0.5 truncate text-xs text-muted">{itemLine}</p>}
      <p role="status" aria-live="polite" className={`mt-3 flex items-center gap-2 text-base font-semibold ${cancelled ? "text-danger-hover" : delivered ? "text-success-strong" : "text-ink"}`}>
        {cancelled ? <XCircle size={19} weight="fill" /> : delivered ? <CheckCircle size={19} weight="fill" /> : live.status === "out_for_delivery" ? <Truck size={19} weight="fill" className="text-accent-ink" /> : <Package size={19} weight="fill" className="text-accent-ink" />}
        <span>{orderHeadline(live)}</span>
      </p>
      {eta && <p className={`mt-1 flex items-center gap-1.5 text-sm ${late ? "font-semibold text-ink" : "text-ink-secondary"}`}><MapPin size={15} weight="fill" className="shrink-0 text-accent-ink" />{eta}</p>}
      {riderLine && !isFinal(live) && <p className="mt-1 flex items-center gap-1.5 text-sm text-ink-secondary"><Phone size={15} weight="fill" className="shrink-0 text-accent-ink" />{riderLine}</p>}
      {delivered && deliveredLine && <p className="mt-1 flex items-center gap-1.5 text-sm text-ink-secondary"><Clock size={15} className="shrink-0 text-success" />{deliveredLine}</p>}
      {placed && !cancelled && <p className="mt-1 flex items-center gap-1.5 text-xs text-muted"><Clock size={13} className="shrink-0" />{placed}</p>}
      <ol className="mt-4 grid grid-cols-4 gap-1.5" aria-label="Order steps">
        {steps.map((step) => <li key={step.id} className="min-w-0">
          <span aria-hidden className={`block h-1.5 rounded-full ${step.state === "cancelled" ? "bg-danger" : step.state === "done" ? "bg-success" : step.state === "current" ? "bg-accent" : "bg-surface-strong"}`} />
          <span className={`mt-1.5 flex items-center gap-1 text-[11px] leading-4 ${step.state === "todo" ? "text-muted" : "font-semibold text-ink-secondary"}`}>
            {step.state === "done" ? <CheckCircle size={12} weight="fill" className="shrink-0 text-success" /> : step.state === "cancelled" ? <XCircle size={12} weight="fill" className="shrink-0 text-danger" /> : <Circle size={11} weight={step.state === "current" ? "fill" : "regular"} className={`shrink-0 ${step.state === "current" ? "text-accent" : "text-subtle"}`} />}
            <span className="truncate">{step.label}<span className="sr-only">{step.state === "done" ? " (done)" : step.state === "current" ? " (now)" : step.state === "cancelled" ? "" : " (not yet)"}</span></span>
          </span>
        </li>)}
      </ol>
      {!live.monitorId && <p className="mt-3 rounded-2xl bg-surface-muted px-3.5 py-2.5 text-xs leading-5 text-ink-secondary">Live status isn&apos;t available for this order here. Track order to try again, or follow it in the {live.store} app.</p>}
      {stopped && live.monitorId && <p className="mt-3 rounded-2xl bg-surface-muted px-3.5 py-2.5 text-xs leading-5 text-ink-secondary">Live updates stopped. This is the last status {live.store} reported.</p>}
      <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-center">
        <button type="button" onClick={() => setTrackOpen(true)} className="rounded-full bg-inverse px-5 py-2.5 text-sm font-semibold text-on-inverse transition hover:bg-inverse-hover active:scale-[.98]">Track order</button>
        <p className="flex items-center gap-1.5 text-xs leading-5 text-muted"><DeviceMobile size={15} className="shrink-0" />Open the {live.store} app to see the rider{isFinal(live) ? "" : " or to cancel"}.</p>
      </div>
      {links.length > 0 && <nav aria-label="Order actions" className={`-mx-1 mt-3 grid gap-1 border-t border-border pt-2 ${links.length === 3 ? "grid-cols-3" : links.length === 2 ? "grid-cols-2" : "grid-cols-1"}`}>
        {links.map((link) => <button key={link.id} type="button" onClick={() => openLink(link)} className={`flex min-h-14 min-w-0 flex-col items-center justify-center gap-1 rounded-xl px-1 py-1.5 text-center text-[11px] font-semibold leading-tight transition-colors hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus ${links.length === 1 ? "flex-row justify-start gap-1.5 px-2 text-xs" : ""} ${link.id === "delivery-line" && deliveryLineReady ? "text-success-strong" : "text-accent-ink"}`}>
          {link.id === "delivery-line" ? <PhoneIncoming size={17} weight={deliveryLineReady ? "fill" : "bold"} className="shrink-0" aria-hidden="true" /> : link.id === "directions" ? <NavigationArrow size={17} weight="bold" className="shrink-0" aria-hidden="true" /> : <Lifebuoy size={17} weight="bold" className="shrink-0" aria-hidden="true" />}<span className="max-w-full">{link.label}</span>
        </button>)}
      </nav>}
    </section>
    <InputDialog open={trackOpen} title="Track order" description={`${live.store} order #${live.orderId}. Updates are read-only.`} onClose={() => setTrackOpen(false)}>
      <LiveOrderTracking initialProvider="zepto" initialOrderId={live.orderId} initialMonitorId={live.monitorId ?? undefined} />
    </InputDialog>
  </motion.div>;
}
