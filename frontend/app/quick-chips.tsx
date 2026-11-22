"use client";

import { ArrowCounterClockwise, CircleNotch, ListChecks, MapPin, Plus, Truck, Warning } from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, useApiRequest } from "./authenticated-api";
import { BASKET_CHANGED_EVENT, useSetBasketLineQuantity, type ShoppingBasket } from "./basket/BasketPanel";
import { addReportText, chipUserText, quickChips, undoQuantity, type AddReport, type Chip, type HeaderChip, type QuickActions, type SavedList, type Store } from "./quick-actions.mjs";
import type { ListsDialogMode } from "./saved-lists-dialog";

const CHIP_ICONS = {
  repeat: ArrowCounterClockwise,
  where: MapPin,
  recent: Plus,
  lists: ListChecks,
} as const;

/**
 * One row of one-tap shortcuts. Scrolls sideways inside its own box (never the
 * page) with a soft fade at the edges; each chip is a real button.
 */
export function QuickChips({ chips, busyKey, disabled, onChip, className = "" }: {
  chips: Chip[];
  busyKey: string | null;
  disabled?: boolean;
  onChip: (chip: Chip) => void;
  className?: string;
}) {
  if (!chips.length) return null;
  return (
    <div className={`relative min-w-0 ${className}`}>
      <ul aria-label="Quick actions" className="flex snap-x gap-1.5 overflow-x-auto overscroll-x-contain px-1 py-1 [-ms-overflow-style:none] [mask-image:linear-gradient(to_right,transparent,black_8px,black_calc(100%-16px),transparent)] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {chips.map((chip) => {
          const Icon = CHIP_ICONS[chip.action.kind];
          const busy = busyKey === chip.key;
          return (
            <li key={chip.key} className="shrink-0 snap-start">
              <button
                type="button"
                onClick={() => onChip(chip)}
                disabled={disabled || busyKey !== null}
                aria-label={chip.description}
                aria-busy={busy}
                className="flex h-9 max-w-[14rem] items-center gap-1.5 rounded-full border border-border bg-surface px-3 text-xs font-medium text-ink-secondary shadow-[0_6px_18px_-14px_rgb(var(--shadow)/.6)] transition-[transform,background-color,border-color] duration-150 ease-out hover:border-accent-border hover:bg-accent-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-surface-muted active:scale-[.97] disabled:cursor-not-allowed disabled:opacity-50 motion-reduce:transition-colors motion-reduce:active:scale-100"
              >
                {busy ? <CircleNotch size={14} className="shrink-0 animate-spin text-accent-ink motion-reduce:animate-none" aria-hidden /> : <Icon size={14} weight="bold" className="shrink-0 text-accent-ink" aria-hidden />}
                <span className="truncate">{chip.action.kind === "recent" ? chip.label.replace(/^\+ /, "") : chip.label}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** The header's live order chip; tapping it scrolls to the order's status card. */
export function HeaderOrderChip({ chip, onOpen }: { chip: HeaderChip; onOpen: (orderId: string) => void }) {
  const Icon = chip.tone === "late" ? Warning : chip.tone === "moving" ? Truck : MapPin;
  return (
    <button
      type="button"
      onClick={() => onOpen(chip.orderId)}
      aria-label={chip.label}
      className={`flex h-9 min-w-0 max-w-[13rem] items-center gap-1.5 rounded-full px-3 text-[12px] font-semibold transition-[transform,background-color] duration-150 ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-surface active:scale-[.97] motion-reduce:active:scale-100 sm:max-w-xs ${chip.tone === "late" ? "bg-warning-soft text-warning hover:bg-warning-border" : "bg-accent-soft text-accent-strong hover:bg-accent-border"}`}
    >
      <Icon size={14} weight="fill" className="shrink-0" aria-hidden />
      <span className="truncate">{chip.text}</span>
    </button>
  );
}

export type QuickToast = { id: number; text: string; undo?: () => Promise<void> };

type Addresses = { swiggy_address_id: string | null; zepto_address_id: string | null };

/**
 * State and actions behind the quick chips and the lists popup. The page owns
 * the timeline: it is told what the user did (a user turn), what happened (an
 * assistant line) and which baskets changed (their cards move to now).
 */
export function useQuickActions({ ready, addresses, onUserAction, onAssistantNote, onBaskets, onAsk }: {
  ready: boolean;
  addresses: () => Addresses;
  onUserAction: (text: string) => void;
  onAssistantNote: (text: string) => void;
  onBaskets: (baskets: ShoppingBasket[]) => void;
  /** Sends a normal chat turn (used by "Mera order kahan hai?"). */
  onAsk: (text: string) => void;
}) {
  const apiRequest = useApiRequest();
  const setLineQuantity = useSetBasketLineQuantity();
  const [actions, setActions] = useState<QuickActions | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [addingListId, setAddingListId] = useState<string | null>(null);
  const [lists, setLists] = useState<{ open: boolean; mode: ListsDialogMode }>({ open: false, mode: "lists" });
  const [toast, setToast] = useState<QuickToast | null>(null);
  const toastSeq = useRef(0);
  const callbacks = useRef({ addresses, onUserAction, onAssistantNote, onBaskets, onAsk });
  useEffect(() => { callbacks.current = { addresses, onUserAction, onAssistantNote, onBaskets, onAsk }; });

  const reload = useCallback(async () => {
    try {
      setActions(await apiRequest<QuickActions>("/api/quick-actions"));
    } catch {
      // Chips are shortcuts; without them the chat still works.
    }
  }, [apiRequest]);

  useEffect(() => {
    if (!ready) return;
    void reload();
    // A placed order changes "last order" and "recent items"; edits come in bursts.
    let timer: number | null = null;
    const onChange = () => {
      if (timer !== null) window.clearTimeout(timer);
      timer = window.setTimeout(() => { timer = null; void reload(); }, 1500);
    };
    window.addEventListener(BASKET_CHANGED_EVENT, onChange);
    return () => {
      if (timer !== null) window.clearTimeout(timer);
      window.removeEventListener(BASKET_CHANGED_EVENT, onChange);
    };
  }, [ready, reload]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast((current) => current?.id === toast.id ? null : current), 6000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const showToast = useCallback((text: string, undo?: () => Promise<void>) => {
    toastSeq.current += 1;
    setToast({ id: toastSeq.current, text, undo });
  }, []);

  const post = useCallback(<T,>(path: string, body: Record<string, unknown>) => apiRequest<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...callbacks.current.addresses(), ...body }),
  }), [apiRequest]);

  const applyReport = useCallback((report: AddReport<ShoppingBasket>, source: string) => {
    const summary = addReportText(report, { source });
    callbacks.current.onAssistantNote(summary.text);
    if (report.baskets.length) {
      callbacks.current.onBaskets(report.baskets);
      window.dispatchEvent(new Event(BASKET_CHANGED_EVENT));
    }
    return summary;
  }, []);

  const failure = (cause: unknown, fallback: string) => cause instanceof ApiError && cause.status < 500 && cause.message ? cause.message : fallback;

  const run = useCallback(async (chip: Chip) => {
    if (busyKey) return;
    const { action } = chip;
    if (action.kind === "lists") { setLists({ open: true, mode: "lists" }); return; }
    if (action.kind === "where") { callbacks.current.onAsk(chipUserText(chip)); return; }
    callbacks.current.onUserAction(chipUserText(chip));
    setBusyKey(chip.key);
    try {
      if (action.kind === "repeat") {
        const report = await post<AddReport<ShoppingBasket>>("/api/quick-actions/repeat-last", {});
        applyReport(report, `your last ${actions?.last_order?.store ?? ""} order`.replace("  ", " "));
      } else {
        const report = await post<AddReport<ShoppingBasket>>("/api/quick-actions/recent-item", { ref: action.item.ref });
        const added = report.items.find((item) => item.status === "added");
        if (!added) { applyReport(report, action.item.name); return; }
        const basket = report.baskets.find((item) => item.provider === action.item.ref.provider);
        if (basket) { callbacks.current.onBaskets([basket]); window.dispatchEvent(new Event(BASKET_CHANGED_EVENT)); }
        const provider = action.item.ref.provider as Store;
        showToast(`${action.item.name} added to your basket.`, async () => {
          const restore = undoQuantity(basket, action.item.ref, added.quantity);
          if (restore === null) return;
          const next = await setLineQuantity(provider, action.item.ref.variant_id, restore);
          callbacks.current.onBaskets([next]);
          window.dispatchEvent(new Event(BASKET_CHANGED_EVENT));
          callbacks.current.onAssistantNote(`Undone: ${action.item.name} taken out of your basket again.`);
        });
      }
    } catch (cause) {
      callbacks.current.onAssistantNote(failure(cause, "Couldn't reach GharSaathi just now. Nothing was added—try again."));
    } finally {
      setBusyKey(null);
    }
  }, [actions?.last_order?.store, applyReport, busyKey, post, setLineQuantity, showToast]);

  const addList = useCallback(async (list: SavedList) => {
    if (addingListId) return;
    setAddingListId(list.id);
    setLists((current) => ({ ...current, open: false }));
    callbacks.current.onUserAction(`Add my “${list.name}” to the basket`);
    try {
      applyReport(await post<AddReport<ShoppingBasket>>(`/api/lists/${encodeURIComponent(list.id)}/add-to-basket`, {}), `“${list.name}”`);
    } catch (cause) {
      callbacks.current.onAssistantNote(failure(cause, `Couldn't add “${list.name}” just now. Nothing was added—try again.`));
    } finally {
      setAddingListId(null);
    }
  }, [addingListId, applyReport, post]);

  return {
    actions,
    chips: (orderActive: boolean) => quickChips({ actions, orderActive }),
    run,
    busyKey,
    addList,
    addingListId,
    lists,
    openLists: (mode: ListsDialogMode = "lists") => setLists({ open: true, mode }),
    closeLists: () => setLists((current) => ({ ...current, open: false })),
    reload,
    toast,
    dismissToast: () => setToast(null),
  };
}

/** The short confirmation after a one-tap add, with Undo. */
export function QuickToastView({ toast, onDismiss }: { toast: QuickToast; onDismiss: () => void }) {
  const [undoing, setUndoing] = useState(false);
  return (
    <div role="status" aria-live="polite" className="pointer-events-auto flex min-w-0 max-w-[calc(100vw_-_2rem)] items-center gap-3 rounded-full sm:max-w-sm bg-inverse py-2 pl-4 pr-2 text-sm font-medium text-on-inverse shadow-[0_18px_40px_-20px_rgb(var(--shadow)/.8)]">
      <span className="min-w-0 truncate">{toast.text}</span>
      {toast.undo && <button type="button" disabled={undoing} onClick={() => { setUndoing(true); void toast.undo?.().finally(() => { setUndoing(false); onDismiss(); }); }} className="shrink-0 rounded-full bg-inverse-hover px-3 py-1.5 text-xs font-semibold text-on-inverse transition-[transform,background-color] duration-150 ease-out hover:bg-ink-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-on-inverse active:scale-[.97] disabled:opacity-60 motion-reduce:active:scale-100">{undoing ? "Undoing…" : "Undo"}</button>}
    </div>
  );
}
