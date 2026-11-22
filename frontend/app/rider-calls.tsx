"use client";

import { Phone, X } from "@phosphor-icons/react";
import { motion } from "framer-motion";
import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { useApiRequest } from "./authenticated-api";
import {
  RIDER_CALLS_PATH,
  RIDER_POLL_MS,
  RIDER_SETTINGS_PATH,
  newRiderCalls,
  riderCallCardText,
  riderCardsToShow,
  riderToastText,
  type RiderCall,
  type RiderSettings,
} from "./rider-assist.mjs";

export type RiderCardEntry = { card: RiderCall; afterMessageId: string | null };

/**
 * Rider assist in the chat: checks for new rider calls every 30 s while the tab is
 * visible (only for the configured owner), toasts each new one once, marks it seen,
 * and returns the recent calls as timeline cards anchored where they arrived.
 * Browser notifications for the same call (kind `rider_called`) are sent by the
 * backend through the notification service; a tap opens `/?rider_call=<id>`.
 */
export function useRiderCalls({ latestMessageIdRef, onToast }: { latestMessageIdRef: RefObject<string | null>; onToast: (text: string) => void }) {
  const request = useApiRequest();
  const [active, setActive] = useState(false);
  const [calls, setCalls] = useState<RiderCall[]>([]);
  const [dismissed, setDismissed] = useState<Set<string>>(() => new Set());
  const anchors = useRef(new Map<string, string | null>());
  const toasted = useRef(new Set<string>());
  const toastRef = useRef(onToast);
  useEffect(() => { toastRef.current = onToast; });

  useEffect(() => {
    let alive = true;
    void request<RiderSettings>(RIDER_SETTINGS_PATH)
      .then((settings) => { if (alive) setActive(settings.feature_enabled && settings.owner_configured); })
      .catch(() => { /* Rider assist is optional; no card, no polling. */ });
    return () => { alive = false; };
  }, [request]);

  const poll = useCallback(async () => {
    const { calls: loaded } = await request<{ calls: RiderCall[] }>(RIDER_CALLS_PATH);
    for (const call of loaded) {
      if (!anchors.current.has(call.id)) anchors.current.set(call.id, latestMessageIdRef.current ?? null);
    }
    setCalls(loaded);
    for (const call of newRiderCalls(loaded, toasted.current)) {
      toasted.current.add(call.id);
      // An in-progress call is toasted again when it finishes only if still unseen.
      if (call.outcome === "in_progress" && call.urgency !== "high") { toasted.current.delete(call.id); continue; }
      toastRef.current(riderToastText(call));
      void request(`${RIDER_CALLS_PATH}/${encodeURIComponent(call.id)}/seen`, { method: "POST" }).catch(() => undefined);
    }
  }, [latestMessageIdRef, request]);

  useEffect(() => {
    if (!active) return;
    const run = () => { if (document.visibilityState === "visible") void poll().catch(() => undefined); };
    run();
    const timer = window.setInterval(run, RIDER_POLL_MS);
    document.addEventListener("visibilitychange", run);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", run); };
  }, [active, poll]);

  // A tapped `rider_called` notification opens /?rider_call=<id>: show that card.
  useEffect(() => {
    const target = new URLSearchParams(window.location.search).get("rider_call");
    if (!target || !calls.some((call) => call.id === target)) return;
    document.getElementById(`rider-call-${target}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [calls]);

  const cards: RiderCardEntry[] = riderCardsToShow(calls, Date.now(), dismissed).map((card) => ({ card, afterMessageId: anchors.current.get(card.id) ?? null }));
  const dismiss = useCallback((id: string) => setDismissed((current) => new Set(current).add(id)), []);
  return { cards, dismiss };
}

/** Timeline system card: "Rider called · 8:24 am · 'Gate pe hoon, flat number?' — answered from your notes". */
export function RiderCallCard({ call, onDismiss }: { call: RiderCall; onDismiss: (id: string) => void }) {
  return <motion.div id={`rider-call-${call.id}`} initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mt-6 mr-8 flex min-w-0 sm:mr-12">
    <div role="status" className={`flex max-w-full items-start gap-2 rounded-2xl px-3 py-2 text-[12px] leading-5 ${call.urgency === "high" ? "bg-warning-soft text-ink" : "bg-surface-strong text-ink-secondary"}`}>
      <Phone size={14} className="mt-0.5 shrink-0 text-subtle" weight="duotone" />
      <span className="min-w-0"><span className="font-medium">{riderCallCardText(call)}</span>{call.summary && <span className="mt-0.5 block text-[11px] text-muted">{call.summary}</span>}</span>
      <button type="button" onClick={() => onDismiss(call.id)} aria-label="Hide this rider call" className="-mr-1 grid size-7 shrink-0 place-items-center rounded-full text-muted transition hover:bg-border-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"><X size={12} weight="bold" /></button>
    </div>
  </motion.div>;
}
