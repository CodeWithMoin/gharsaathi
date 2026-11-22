"use client";

import { Phone, X } from "@phosphor-icons/react";
import { motion } from "framer-motion";
import { useCallback, useEffect, useRef, useState } from "react";
import { useApiRequest } from "./authenticated-api";
import {
  INBOUND_CALLS_PATH,
  INBOUND_CONFIG_PATH,
  INBOUND_POLL_MS,
  inboundCallCardText,
  inboundCallUrgent,
  inboundCardsToShow,
  inboundHeadline,
  inboundOutcomeText,
  type InboundCall,
} from "./inbound-calls.mjs";

/**
 * Calls to the Sarvam inbound delivery line. Reads the stored, redacted summaries
 * every 30 s while the tab is visible (the backend alone talks to Sarvam), toasts
 * each call that arrives while the app is open, and returns the recent ones as
 * chat cards. A tapped `rider_called` notification opens `/?inbound_call=<id>`.
 */
export function useInboundCalls({ onToast }: { onToast: (text: string) => void }) {
  const request = useApiRequest();
  const [enabled, setEnabled] = useState(false);
  const [calls, setCalls] = useState<InboundCall[]>([]);
  const [dismissed, setDismissed] = useState<Set<string>>(() => new Set());
  const known = useRef<Set<string> | null>(null);
  const toastRef = useRef(onToast);
  useEffect(() => { toastRef.current = onToast; });

  useEffect(() => {
    let alive = true;
    void request<{ enabled: boolean }>(INBOUND_CONFIG_PATH)
      .then((config) => { if (alive) setEnabled(Boolean(config.enabled)); })
      .catch(() => { /* The delivery line is optional; no cards, no polling. */ });
    return () => { alive = false; };
  }, [request]);

  const poll = useCallback(async () => {
    const { calls: loaded } = await request<{ calls: InboundCall[] }>(INBOUND_CALLS_PATH, { cache: "no-store" });
    if (known.current) {
      for (const call of loaded) {
        if (!known.current.has(call.id)) toastRef.current(`${inboundHeadline(call.provider, call.outcome)} · ${inboundOutcomeText(call.outcome, call.provider)}`);
      }
    }
    known.current = new Set(loaded.map((call) => call.id));
    setCalls(loaded);
  }, [request]);

  useEffect(() => {
    if (!enabled) return;
    const run = () => { if (document.visibilityState === "visible") void poll().catch(() => undefined); };
    run();
    const timer = window.setInterval(run, INBOUND_POLL_MS);
    document.addEventListener("visibilitychange", run);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", run); };
  }, [enabled, poll]);

  useEffect(() => {
    const target = new URLSearchParams(window.location.search).get("inbound_call");
    if (!target || !calls.some((call) => call.id === target)) return;
    document.getElementById(`inbound-call-${target}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [calls]);

  const cards = inboundCardsToShow(calls, Date.now(), dismissed);
  const dismiss = useCallback((id: string) => setDismissed((current) => new Set(current).add(id)), []);
  return { calls, cards, dismiss };
}

/** Timeline card: "Zepto rider called · 2:14 pm · Route confirmed — <summary>". */
export function InboundCallCard({ call, onDismiss }: { call: InboundCall; onDismiss: (id: string) => void }) {
  const urgent = inboundCallUrgent(call);
  return <motion.div id={`inbound-call-${call.id}`} initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mt-6 mr-8 flex min-w-0 sm:mr-12">
    <div role="status" className={`flex max-w-full items-start gap-2 rounded-2xl px-3 py-2 text-[12px] leading-5 ${urgent ? "bg-warning-soft text-ink" : "bg-surface-strong text-ink-secondary"}`}>
      <Phone size={14} className="mt-0.5 shrink-0 text-subtle" weight="duotone" aria-hidden />
      <span className="min-w-0">
        <span className="font-medium">{inboundCallCardText(call)}</span>
        {call.caller && <span className="mt-0.5 block text-[11px] text-muted">Caller {call.caller}</span>}
      </span>
      <button type="button" onClick={() => onDismiss(call.id)} aria-label="Hide this call" className="-mr-1 grid size-7 shrink-0 place-items-center rounded-full text-muted transition hover:bg-border-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"><X size={12} weight="bold" /></button>
    </div>
  </motion.div>;
}
