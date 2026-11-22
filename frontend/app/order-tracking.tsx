"use client";

import {
  ArrowsClockwise,
  BellSimple,
  CaretDown,
  CheckCircle,
  Circle,
  ClockCountdown,
  MapPin,
  Package,
  Pause,
  Play,
  Timer,
  Truck,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API_BASE_URL, useAuthenticatedFetch } from "./authenticated-api";
import { DeliveryLinePanel } from "./delivery-line-panel";
import type { SelectedDeliveryAddress } from "./delivery-line.mjs";

const POLL_INTERVAL_MS = 45_000;

type Provider = "swiggy" | "zepto";
type TrackingStatus =
  | "created"
  | "confirmed"
  | "preparing"
  | "packed"
  | "out_for_delivery"
  | "arriving"
  | "delivered"
  | "delayed"
  | "cancelled"
  | "issue"
  | string;

type TrackingEvent = {
  id?: string;
  status?: TrackingStatus;
  label?: string;
  description?: string | null;
  occurred_at?: string | null;
  timestamp?: string | null;
  current?: boolean;
  snapshot?: {
    status?: TrackingStatus;
    provider_status?: string | null;
    message?: string | null;
    eta_at?: string | null;
  };
};

type TrackingOrder = {
  id?: string;
  order_id?: string;
  provider?: Provider;
  status?: TrackingStatus;
  active?: boolean;
  status_label?: string;
  eta?: string | null;
  eta_text?: string | null;
  eta_at?: string | null;
  updated_at?: string | null;
  items?: Array<{ name?: string; quantity?: number }>;
  item_summary?: string | null;
  timeline?: TrackingEvent[];
  events?: TrackingEvent[];
  snapshot?: TrackingEvent["snapshot"] | null;
  last_error?: string | null;
  stop_reason?: string | null;
};

type TrackingResponse = {
  monitor_id?: string;
  order?: TrackingOrder;
  changed?: boolean;
  meaningful_change?: boolean | { message?: string | null; summary?: string | null };
  change_summary?: string | null;
  checked_at?: string | null;
  next_check_at?: string | null;
  live_tracking_supported?: boolean;
  provider_message?: string | null;
};

export type OrderTrackingProps = {
  initialProvider?: Provider;
  initialOrderId?: string;
  initialAddressId?: string;
  /** Open an existing monitor (e.g. the one started for a placed order) instead of the newest watch. */
  initialMonitorId?: string;
  recipientName?: string;
  /** The address selected for each store in the app; the delivery line always uses it. */
  deliveryAddresses?: Partial<Record<Provider, SelectedDeliveryAddress>>;
  /** Opens the app's address picker for that store. */
  onChangeAddress?: (provider: Provider) => void;
  onClose?: () => void;
};

const statusCopy: Record<string, string> = {
  created: "Order received",
  confirmed: "Order confirmed",
  preparing: "Being prepared",
  packed: "Packed and ready",
  out_for_delivery: "Out for delivery",
  arriving: "Arriving soon",
  delivered: "Delivered",
  delayed: "Delayed",
  cancelled: "Cancelled",
  issue: "Needs attention",
};

function statusLabel(status?: TrackingStatus, fallback?: string) {
  return fallback || (status ? statusCopy[status] ?? status.replaceAll("_", " ") : "Waiting for an update");
}

function displayTime(value?: string | null) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-IN", { hour: "numeric", minute: "2-digit" }).format(date);
}

function orderItems(order: TrackingOrder) {
  if (order.item_summary) return order.item_summary;
  const items = order.items?.filter((item) => item.name) ?? [];
  if (!items.length) return null;
  const [first, ...rest] = items;
  const quantity = first.quantity && first.quantity > 1 ? ` ×${first.quantity}` : "";
  return `${first.name}${quantity}${rest.length ? ` + ${rest.length} more` : ""}`;
}

function normaliseResponse(payload: TrackingResponse | TrackingOrder): Required<Pick<TrackingResponse, "checked_at" | "next_check_at">> & TrackingResponse & { order: TrackingOrder } {
  const response = payload as TrackingResponse;
  return {
    ...response,
    order: response.order ?? (payload as TrackingOrder),
    checked_at: response.checked_at ?? null,
    next_check_at: response.next_check_at ?? null,
  };
}

function isProblem(status?: TrackingStatus) {
  return status === "delayed" || status === "cancelled" || status === "issue";
}

function StatusIcon({ status, active }: { status?: TrackingStatus; active: boolean }) {
  const className = active ? "text-accent-ink" : "text-subtle";
  if (status === "out_for_delivery" || status === "arriving") return <Truck size={17} weight={active ? "fill" : "regular"} className={className} />;
  if (status === "delivered") return <CheckCircle size={17} weight="fill" className="text-success" />;
  if (isProblem(status)) return <WarningCircle size={17} weight="fill" className="text-danger" />;
  if (status === "packed") return <Package size={17} weight={active ? "fill" : "regular"} className={className} />;
  return active ? <CheckCircle size={17} weight="fill" className={className} /> : <Circle size={13} className={className} />;
}

export function LiveOrderTracking({
  initialProvider = "swiggy",
  initialOrderId = "",
  initialAddressId,
  initialMonitorId,
  recipientName = "",
  deliveryAddresses,
  onChangeAddress,
  onClose,
}: OrderTrackingProps) {
  const authenticatedFetch = useAuthenticatedFetch();
  const [provider, setProvider] = useState<Provider>(initialProvider);
  const [orderId, setOrderId] = useState(initialOrderId);
  const [monitorId, setMonitorId] = useState<string | null>(null);
  const [tracking, setTracking] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(true);
  const [data, setData] = useState<ReturnType<typeof normaliseResponse> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [lastChange, setLastChange] = useState("");
  const requestInFlight = useRef(false);
  const seenEventCount = useRef(0);

  const fetchUpdate = useCallback(async (force = false, nextMonitorId = monitorId) => {
    if (!nextMonitorId || requestInFlight.current) return;
    requestInFlight.current = true;
    setLoading(true);
    setError("");
    try {
      const response = await authenticatedFetch(`${API_BASE_URL}/api/tracking/monitors/${encodeURIComponent(nextMonitorId)}${force ? "/refresh" : ""}`, {
        method: force ? "POST" : "GET",
        cache: "no-store",
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.detail || "No saved tracking update is available yet.");
      const next = normaliseResponse(payload as TrackingResponse | TrackingOrder);
      setData(next);
      setTracking(next.live_tracking_supported !== false && next.order.active !== false);
      const meaningful = typeof next.meaningful_change === "object" ? next.meaningful_change.message ?? next.meaningful_change.summary : next.change_summary;
      const eventCount = next.order.events?.length ?? 0;
      const newSavedEvent = seenEventCount.current > 0 && eventCount > seenEventCount.current;
      seenEventCount.current = eventCount;
      setLastChange(next.changed || newSavedEvent || next.meaningful_change === true || Boolean(meaningful) ? meaningful || "Your order status changed." : "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load this order.");
    } finally {
      requestInFlight.current = false;
      setLoading(false);
    }
  }, [authenticatedFetch, monitorId]);

  useEffect(() => {
    if (!initialMonitorId) return;
    let active = true;
    void authenticatedFetch(`${API_BASE_URL}/api/tracking/monitors/${encodeURIComponent(initialMonitorId)}`, { cache: "no-store" }).then(async (response) => {
      if (!response.ok || !active) return;
      const next = normaliseResponse(await response.json() as TrackingResponse);
      if (!active) return;
      setMonitorId(initialMonitorId);
      setProvider(next.order.provider ?? initialProvider);
      setOrderId(next.order.order_id ?? initialOrderId);
      seenEventCount.current = next.order.events?.length ?? 0;
      setData(next);
      setTracking(next.live_tracking_supported !== false && next.order.active !== false);
    }).catch(() => {
      // The order ID stays filled in, so tracking can still be started by hand.
    });
    return () => { active = false; };
  }, [authenticatedFetch, initialMonitorId, initialOrderId, initialProvider]);

  useEffect(() => {
    if (initialOrderId || initialMonitorId) return;
    let active = true;
    void authenticatedFetch(`${API_BASE_URL}/api/tracking/monitors?active_only=true`, {
      cache: "no-store",
    }).then(async (response) => {
      if (!response.ok || !active) return;
      const watches = await response.json() as TrackingOrder[];
      const watch = watches[0];
      if (!watch?.id || !active) return;
      setMonitorId(watch.id);
      setProvider(watch.provider ?? "swiggy");
      setOrderId(watch.order_id ?? "");
      setTracking(true);
      seenEventCount.current = watch.events?.length ?? 0;
      setData(normaliseResponse(watch));
    }).catch(() => {
      // Starting a new monitor remains available if no saved watch can be restored.
    });
    return () => { active = false; };
  }, [authenticatedFetch, initialMonitorId, initialOrderId]);

  useEffect(() => {
    if (!tracking || !monitorId) return;
    void fetchUpdate(false);
    const interval = window.setInterval(() => void fetchUpdate(false), POLL_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, [fetchUpdate, monitorId, tracking]);

  const startTracking = async (event: FormEvent) => {
    event.preventDefault();
    const cleanedOrderId = orderId.trim();
    if (!cleanedOrderId) return;
    if (requestInFlight.current) return;
    requestInFlight.current = true;
    setLoading(true);
    setError("");
    setData(null);
    setLastChange("");
    try {
      const response = await authenticatedFetch(`${API_BASE_URL}/api/tracking/monitors`, {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider,
          order_id: cleanedOrderId,
          address_id: provider === "swiggy" ? initialAddressId : undefined,
        }),
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.detail || "Could not start tracking this order.");
      const next = normaliseResponse(payload as TrackingResponse | TrackingOrder);
      if (!next.monitor_id) throw new Error("Tracking could not be started safely. Please try again.");
      setMonitorId(next.monitor_id);
      setData(next);
      setTracking(next.live_tracking_supported !== false);
      seenEventCount.current = next.order.events?.length ?? 0;
      const meaningful = typeof next.meaningful_change === "object" ? next.meaningful_change.message ?? next.meaningful_change.summary : next.change_summary;
      setLastChange(next.changed || next.meaningful_change === true || Boolean(meaningful) ? meaningful || "Your order status changed." : "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not start tracking this order.");
    } finally {
      requestInFlight.current = false;
      setLoading(false);
    }
  };

  const stopTracking = async () => {
    if (!monitorId || requestInFlight.current) return;
    requestInFlight.current = true;
    setLoading(true);
    setError("");
    try {
      const response = await authenticatedFetch(`${API_BASE_URL}/api/tracking/monitors/${encodeURIComponent(monitorId)}`, {
        method: "DELETE",
        cache: "no-store",
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.detail || "Could not stop this order watch.");
      setData(normaliseResponse(payload as TrackingResponse));
      setTracking(false);
      setLastChange("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not stop this order watch.");
    } finally {
      requestInFlight.current = false;
      setLoading(false);
    }
  };

  const order = data?.order;
  const timeline = useMemo(() => {
    const source = order?.timeline ?? order?.events ?? [];
    if (source.length) return source.map((event) => event.snapshot ? {
      ...event,
      status: event.snapshot.status,
      label: event.snapshot.provider_status ?? statusLabel(event.snapshot.status),
      description: event.snapshot.message,
    } : event);
    return order?.status ? [{ status: order.status, label: statusLabel(order.status, order.status_label), current: true, occurred_at: order.updated_at }] : [];
  }, [order]);
  const etaAt = order?.snapshot?.eta_at ?? order?.eta_at;
  const eta = order?.eta_text ?? order?.eta ?? (etaAt ? `By ${displayTime(etaAt)}` : null);
  const currentStatus = order?.snapshot?.status ?? order?.status;
  const checkedAt = displayTime(data?.checked_at ?? order?.updated_at);

  return (
    <section className="w-full overflow-hidden rounded-[8px_22px_22px_22px] border border-border-strong bg-surface shadow-[0_18px_45px_-34px_rgb(var(--shadow)/.7)]" aria-label="Live order tracking">
      <header className="flex items-start gap-3 border-b border-border px-4 py-3.5">
        <div className="grid size-9 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent-ink"><ClockCountdown size={19} weight="fill" /></div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-[15px] font-semibold text-ink">Track an order</h2>
            {tracking && <span className="flex shrink-0 items-center gap-1 rounded-full bg-success-soft px-2 py-1 text-[9px] font-bold uppercase tracking-[.08em] text-success"><span className="size-1.5 animate-pulse rounded-full bg-success" />Watching</span>}
          </div>
          <p className="mt-0.5 text-[11px] leading-4 text-muted">Read-only updates from GharSaathi’s saved order status.</p>
        </div>
        {onClose && <button type="button" onClick={onClose} aria-label="Close live order tracking" className="grid size-8 shrink-0 place-items-center rounded-full text-muted transition hover:bg-surface-strong"><X size={16} /></button>}
      </header>

      <div className="p-4">
        <form onSubmit={startTracking} className="space-y-2.5">
          <div className="grid grid-cols-2 gap-2">
            {(["swiggy", "zepto"] as const).map((name) => <button key={name} type="button" onClick={() => { setProvider(name); setData(null); setTracking(false); setMonitorId(null); }} className={`rounded-xl border px-3 py-2 text-xs font-semibold capitalize transition ${provider === name ? "border-accent bg-accent-soft text-accent-strong" : "border-border-strong text-ink-secondary"}`}>{name}</button>)}
          </div>
          <div className="flex gap-2">
            <label className="min-w-0 flex-1"><span className="sr-only">Order ID</span><input value={orderId} onChange={(event) => setOrderId(event.target.value)} required placeholder="Enter order ID" className="w-full rounded-xl border border-border-strong bg-white px-3 py-2.5 text-sm outline-none placeholder:text-subtle focus:border-accent" /></label>
            {!tracking ? <button type="submit" disabled={loading} className="flex shrink-0 items-center justify-center gap-1.5 rounded-full bg-accent px-4 py-2.5 text-xs font-semibold text-on-accent shadow-[0_8px_22px_-12px_rgb(var(--accent-shadow)/.8)] transition active:scale-[.97] disabled:opacity-50"><Play size={13} weight="fill" />Start</button> : <button type="button" onClick={() => void stopTracking()} disabled={loading} className="flex shrink-0 items-center justify-center gap-1.5 rounded-full border border-border-strong px-4 py-2.5 text-xs font-semibold text-ink-secondary transition active:scale-[.97] disabled:opacity-50"><Pause size={13} weight="fill" />Stop</button>}
          </div>
        </form>

        <DeliveryLinePanel
          provider={provider}
          address={deliveryAddresses?.[provider] ?? null}
          recipientName={recipientName}
          onChangeAddress={onChangeAddress ? () => onChangeAddress(provider) : undefined}
          refreshKey={`${monitorId ?? ""}:${currentStatus ?? ""}:${tracking}`}
        />

        {error && <div role="alert" className="mt-3 rounded-2xl border border-danger-border bg-danger-soft px-3 py-2.5 text-xs leading-5 text-danger"><div className="flex items-center gap-1.5 font-semibold"><WarningCircle size={15} weight="fill" />Couldn’t show this order</div><p className="mt-0.5">{error}</p></div>}

        {loading && !order && <div className="mt-4 space-y-3" aria-live="polite"><div className="h-20 animate-pulse rounded-2xl bg-surface-muted" /><div className="h-10 animate-pulse rounded-xl bg-canvas" /></div>}

        {!loading && !error && !order && !tracking && <div className="mt-4 rounded-2xl bg-surface-muted px-3.5 py-3 text-xs leading-5 text-muted"><div className="flex items-center gap-1.5 font-semibold text-ink-secondary"><Timer size={15} />Ready when you are</div><p className="mt-1">Add an order ID to save a watch for meaningful delivery changes. It continues through browser reloads.</p></div>}

        {order && <div className="mt-4 space-y-3">
          {lastChange && <div className="rounded-2xl border border-success-border bg-success-soft px-3 py-2.5 text-xs leading-5 text-success-strong"><div className="flex items-center gap-1.5 font-semibold"><BellSimple size={15} weight="fill" />Meaningful update</div><p className="mt-0.5">{lastChange}</p></div>}

          <div className={`rounded-2xl border px-3.5 py-3 ${isProblem(currentStatus) ? "border-danger-border bg-danger-soft" : "border-border bg-surface"}`}>
            <div className="flex items-start gap-3">
              <div className="grid size-9 shrink-0 place-items-center rounded-xl bg-white shadow-[0_6px_16px_-12px_rgb(var(--shadow)/.65)]"><StatusIcon status={currentStatus} active /></div>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-ink">{statusLabel(currentStatus, order.status_label)}</p>
                {orderItems(order) && <p className="mt-0.5 truncate text-[11px] text-muted">{orderItems(order)}</p>}
                {eta && <p className="mt-2 flex items-center gap-1.5 text-xs font-semibold text-ink-secondary"><MapPin size={14} weight="fill" />ETA {eta}</p>}
              </div>
            </div>
          </div>

          {timeline.length > 0 && <div className="rounded-2xl border border-border bg-white">
            <button type="button" onClick={() => setDetailsOpen((open) => !open)} aria-expanded={detailsOpen} className="flex w-full items-center gap-2 px-3.5 py-3 text-left"><Package size={16} className="text-accent-strong" /><span className="min-w-0 flex-1 text-xs font-semibold text-ink-secondary">Order journey</span><CaretDown size={15} className={`text-muted transition ${detailsOpen ? "rotate-180" : ""}`} /></button>
            {detailsOpen && <ol className="border-t border-border px-3.5 py-1.5">{timeline.map((event, index) => {
              const active = event.current ?? index === timeline.length - 1;
              const time = displayTime(event.occurred_at ?? event.timestamp);
              return <li key={event.id ?? `${event.status ?? "status"}-${index}`} className="relative flex gap-3 py-2.5 last:pb-2"><div className="relative z-10 grid size-5 shrink-0 place-items-center rounded-full bg-white"><StatusIcon status={event.status} active={active} /></div>{index < timeline.length - 1 && <span className="absolute left-[9px] top-7 h-[calc(100%-8px)] w-px bg-border" />}<div className="min-w-0 flex-1"><p className={`text-xs ${active ? "font-semibold text-ink" : "text-muted"}`}>{event.label ?? statusLabel(event.status)}</p>{event.description && <p className="mt-0.5 text-[11px] leading-4 text-muted">{event.description}</p>}{time && <time className="mt-0.5 block text-[10px] text-subtle">{time}</time>}</div></li>;
            })}</ol>}
          </div>}

          {data?.live_tracking_supported === false && <div className="rounded-2xl border border-border-strong bg-canvas px-3 py-2.5 text-xs leading-5 text-ink-secondary"><div className="flex items-center gap-1.5 font-semibold"><WarningCircle size={15} />Live tracking is not available for this provider</div><p className="mt-0.5">{data.provider_message || "Showing the latest saved order status only. We will not guess or repeatedly call the provider."}</p></div>}
          <div className="flex items-center gap-2 px-0.5 text-[10px] leading-4 text-muted"><Timer size={13} className="shrink-0" />{tracking ? `Shows GharSaathi’s saved updates every ${POLL_INTERVAL_MS / 1000} sec; provider cadence stays protected` : "Watching is stopped"}{checkedAt && ` · Last saved ${checkedAt}`}</div>
          <button type="button" onClick={() => void fetchUpdate(true)} disabled={loading || !monitorId} className="flex w-full items-center justify-center gap-1.5 rounded-full border border-border-strong px-4 py-2.5 text-xs font-semibold text-ink-secondary transition hover:bg-canvas active:scale-[.98] disabled:opacity-50"><ArrowsClockwise size={14} className={loading ? "animate-spin" : ""} />Refresh saved update</button>
        </div>}
      </div>
    </section>
  );
}
