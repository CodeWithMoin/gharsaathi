"use client";

import { ArrowsLeftRight, BookmarkSimple, Basket, CircleNotch, Minus, Plus, Receipt, Trash, X } from "@phosphor-icons/react";
import { AnimatePresence, motion, useDragControls, useReducedMotion, type PanInfo } from "framer-motion";
import { type KeyboardEvent, type PointerEvent, useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ApiError, useApiRequest } from "./authenticated-api";
import { BASKET_CHANGED_EVENT, useSetBasketLineQuantity, type ApprovalRequest, type BasketLine, type ProviderName, type ShoppingBasket } from "./basket/BasketPanel";
import { EMPTY_TITLE, FEES_NOTE, STORE_NAMES, afterBill, billAvailability, emptyStateChips, itemCount, sheetView, shouldDismiss } from "./cart-sheet-state.mjs";
import { DRAWER_SPRING } from "./drawer-push.mjs";
import { CLEAR_CONFLICT_TEXT, basketEditState, clearBasketCopy, type Chip } from "./quick-actions.mjs";
import { QuickChips } from "./quick-chips";
import { ConfirmDialog } from "./ui/confirm-dialog";
import { FOCUSABLE_SELECTOR, nextFocusIndex } from "./ui/input-dialog-logic.mjs";

const MAX_QUANTITY = 50;

type StoreReadiness = { connected: boolean; addressId: string };

export type CartSheetProps = {
  open: boolean;
  onClose: () => void;
  /** The store the header cart points at; the sheet opens on its tab. */
  preferred: ProviderName;
  baskets: Partial<Record<ProviderName, ShoppingBasket>>;
  commerceMode: "demo" | "live";
  stores: Record<ProviderName, StoreReadiness>;
  /** The store whose last order may still be going through (outcome unknown, payment in progress). */
  orderInFlightStore: ProviderName | null;
  onBasketChange: (basket: ShoppingBasket) => void;
  onSwap: (provider: ProviderName, line: BasketLine) => void;
  onSaveAsList: () => void;
  /** A fresh bill: the page puts the bill card at the bottom of the chat. */
  onBill: (provider: ProviderName, request: ApprovalRequest) => void;
  chips: Chip[];
  chipsBusyKey: string | null;
  chipsDisabled?: boolean;
  onChip: (chip: Chip) => void;
};

/**
 * The header cart: a bottom sheet over the dimmed chat. Edits use the basket line
 * API; "Get final bill" asks the store for the exact bill and hands it to the chat,
 * where it is approved. Nothing here approves or places an order.
 */
export function CartSheet({ open, onClose, preferred, baskets, commerceMode, stores, orderInFlightStore, onBasketChange, onSwap, onSaveAsList, onBill, chips, chipsBusyKey, chipsDisabled, onChip }: CartSheetProps) {
  const reduceMotion = useReducedMotion() ?? false;
  const apiRequest = useApiRequest();
  const setLineQuantity = useSetBasketLineQuantity();
  const dragControls = useDragControls();
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement | null>(null);
  const restoreRef = useRef<HTMLElement | null>(null);
  const [mounted, setMounted] = useState(false);
  const [tab, setTab] = useState<ProviderName | null>(null);
  const [action, setAction] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);
  const [clearOpen, setClearOpen] = useState(false);
  const [clearError, setClearError] = useState("");
  useEffect(() => setMounted(true), []);

  const view = sheetView(baskets, tab ?? preferred);
  const provider = view.tab;
  const basket = provider ? baskets[provider] ?? null : null;
  const storeName = provider ? STORE_NAMES[provider] : "";

  // Fresh on every open: the same reads the basket card makes.
  useEffect(() => {
    if (!open) return;
    setTab(null);
    setNotice(null);
    let active = true;
    for (const store of ["zepto", "swiggy_instamart"] as const) {
      void apiRequest<ShoppingBasket>(`/api/basket?provider=${store}`).then((next) => { if (active) onBasketChange(next); }).catch(() => undefined);
    }
    return () => { active = false; };
  }, [apiRequest, onBasketChange, open]);

  useLayoutEffect(() => {
    if (!open) return;
    restoreRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const frame = window.requestAnimationFrame(() => panelRef.current?.focus({ preventScroll: true }));
    return () => {
      window.cancelAnimationFrame(frame);
      document.body.style.overflow = previousOverflow;
      const restore = restoreRef.current;
      queueMicrotask(() => { if (restore && document.contains(restore)) restore.focus({ preventScroll: true }); });
    };
  }, [open]);

  const changed = useCallback((next: ShoppingBasket) => {
    onBasketChange(next);
    window.dispatchEvent(new Event(BASKET_CHANGED_EVENT));
  }, [onBasketChange]);

  const changeQuantity = async (line: BasketLine, quantity: number) => {
    if (!provider) return;
    setAction(`line-${line.ref.variant_id}`);
    try {
      changed(await setLineQuantity(provider, line.ref.variant_id, quantity));
      setNotice(quantity <= 0 ? { text: `${line.name} removed.`, error: false } : null);
    } catch (cause) {
      setNotice({ text: cause instanceof Error ? cause.message : "Couldn't change the quantity. Try again.", error: true });
    } finally {
      setAction(null);
    }
  };

  const clearBasket = async () => {
    if (!basket) return;
    setAction("clear");
    setClearError("");
    try {
      changed(await apiRequest<ShoppingBasket>(`/api/baskets/${encodeURIComponent(basket.provider)}/clear`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_revision: basket.revision }) }));
      setClearOpen(false);
      setNotice({ text: "Basket cleared.", error: false });
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) {
        setClearOpen(false);
        setNotice({ text: CLEAR_CONFLICT_TEXT, error: true });
        void apiRequest<ShoppingBasket>(`/api/basket?provider=${basket.provider}`).then(onBasketChange).catch(() => undefined);
      } else setClearError(cause instanceof Error ? cause.message : "Couldn't clear the basket. Try again.");
    } finally {
      setAction(null);
    }
  };

  const store = provider ? stores[provider] : null;
  const bill = billAvailability({ basket, connected: store?.connected ?? false, addressId: store?.addressId, orderInFlight: provider !== null && orderInFlightStore === provider, busy: action !== null });

  const getBill = async () => {
    if (!basket || !store || bill.disabled) return;
    const target = basket.provider;
    const query = `?provider=${encodeURIComponent(target)}`;
    setAction("bill");
    setNotice(null);
    try {
      // The bill is for the address selected in the app.
      if (basket.address_id !== store.addressId) {
        onBasketChange(await apiRequest<ShoppingBasket>(`/api/basket/address${query}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ address_id: store.addressId }) }));
      }
      const request = await apiRequest<ApprovalRequest>(`/api/basket/approval${query}`, { method: "POST" });
      onBasketChange(request.basket);
      const { emit } = afterBill({ open, tab }, target, request);
      onBill(emit.provider, emit.request);
      onClose();
    } catch (cause) {
      setNotice({ text: cause instanceof Error ? cause.message : "Couldn't get the final bill. Try again.", error: true });
    } finally {
      setAction(null);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const panel = panelRef.current;
    if (!panel || !(event.target instanceof Node) || !panel.contains(event.target)) return;
    if (event.key === "Escape") { event.stopPropagation(); onClose(); return; }
    if (event.key !== "Tab") return;
    const focusables = [...panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)].filter((element) => element.offsetParent !== null);
    const index = nextFocusIndex(focusables.length, focusables.indexOf(document.activeElement as HTMLElement), event.shiftKey);
    event.preventDefault();
    (index < 0 ? panel : focusables[index]).focus();
  };

  const onTabKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    const index = view.stores.findIndex((item) => item.provider === provider);
    const next = view.stores[(index + (event.key === "ArrowRight" ? 1 : view.stores.length - 1)) % view.stores.length];
    if (!next) return;
    event.preventDefault();
    setTab(next.provider);
    window.requestAnimationFrame(() => document.getElementById(`${titleId}-tab-${next.provider}`)?.focus());
  };

  const startDrag = (event: PointerEvent<HTMLDivElement>) => {
    if (reduceMotion || (event.target instanceof HTMLElement && event.target.closest("button"))) return;
    dragControls.start(event);
  };
  const endDrag = (_: unknown, info: PanInfo) => {
    if (shouldDismiss({ offset: info.offset.y, velocity: info.velocity.y, height: panelRef.current?.offsetHeight ?? 0 })) onClose();
  };

  const lines = basket && itemCount(basket) > 0 ? basket.lines : [];
  const edit = basketEditState(basket, { busy: action !== null });
  const frozen = basket?.status === "checkout_pending" || basket?.status === "ordered";
  const clearCopy = basket ? clearBasketCopy(basket) : null;
  const recentChips = emptyStateChips(chips);
  const subtitle = `${view.totalCount} ${view.totalCount === 1 ? "item" : "items"}${view.showTabs ? " · 2 stores" : ` · ${storeName}`}`;

  const panelMotion = reduceMotion
    ? { initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 }, transition: { duration: 0.15 } }
    : { initial: { y: "100%" }, animate: { y: 0 }, exit: { y: "100%" }, transition: DRAWER_SPRING };

  return <>
    {mounted && createPortal(
      <AnimatePresence>
        {open && <motion.div key="cart-sheet" className="fixed inset-0 z-[80] flex items-end justify-center" initial={{ opacity: 1 }} animate={{ opacity: 1 }} exit={{ opacity: 1 }}>
          <motion.div aria-hidden="true" className="absolute inset-0 bg-scrim/40" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: reduceMotion ? 0.15 : 0.2, ease: "easeOut" }} onMouseDown={onClose} data-testid="cart-sheet-backdrop" />
          <motion.div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            tabIndex={-1}
            onKeyDown={onKeyDown}
            drag={reduceMotion ? false : "y"}
            dragListener={false}
            dragControls={dragControls}
            dragConstraints={{ top: 0, bottom: 0 }}
            dragElastic={{ top: 0.04, bottom: 1 }}
            onDragEnd={endDrag}
            {...panelMotion}
            className="relative flex max-h-[85dvh] w-full max-w-lg flex-col rounded-t-[28px] bg-surface text-ink shadow-[0_-24px_70px_-30px_rgb(var(--shadow)/.6)] outline-none"
          >
            <div onPointerDown={startDrag} className="shrink-0 touch-none select-none px-6 pb-4 pt-3">
              <span className="mx-auto block h-1 w-10 rounded-full bg-border-strong" aria-hidden="true" />
              <div className="mt-4 flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <h2 id={titleId} className="font-display text-xl font-semibold tracking-[-.02em]">Your basket</h2>
                  {!view.empty && <p className="mt-0.5 text-sm text-muted">{subtitle}</p>}
                </div>
                <button type="button" onClick={onClose} aria-label="Close basket" className="-mr-2 grid size-11 shrink-0 place-items-center rounded-full text-muted transition-[transform,background-color] duration-150 ease-out hover:bg-surface-muted hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus active:scale-[.96] motion-reduce:active:scale-100"><X size={20} /></button>
              </div>
              {view.showTabs && <div role="tablist" aria-label="Store" onKeyDown={onTabKey} className="mt-4 grid grid-cols-2 gap-1 rounded-full bg-surface-muted p-1">
                {view.stores.map((item) => {
                  const selected = item.provider === provider;
                  return <button key={item.provider} id={`${titleId}-tab-${item.provider}`} type="button" role="tab" aria-selected={selected} tabIndex={selected ? 0 : -1} onClick={() => setTab(item.provider)} className={`h-10 rounded-full text-sm font-semibold transition-[background-color,color,box-shadow] duration-150 ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus ${selected ? "bg-surface text-ink shadow-[0_4px_14px_-8px_rgb(var(--shadow)/.6)]" : "text-muted hover:text-ink"}`}>{item.label} <span className="tabular-nums text-subtle">{item.count}</span></button>;
                })}
              </div>}
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain border-t border-border px-6" role={view.showTabs ? "tabpanel" : undefined} aria-labelledby={view.showTabs && provider ? `${titleId}-tab-${provider}` : undefined}>
              {view.empty ? <div className="flex flex-col items-center py-12 text-center">
                <span className="grid size-14 place-items-center rounded-2xl bg-surface-muted text-accent-ink"><Basket size={26} /></span>
                <p className="mt-4 text-base font-semibold">{EMPTY_TITLE}</p>
                <p className="mt-1.5 max-w-[30ch] text-sm leading-6 text-muted">{recentChips.length ? "Add something you buy often, or just say what you need." : "Just say or type what you need."}</p>
                {recentChips.length > 0 && <QuickChips chips={recentChips} busyKey={chipsBusyKey} disabled={chipsDisabled} onChip={onChip} className="mt-5 w-full" />}
              </div> : <>
                <ul className="divide-y divide-border" aria-label={`${storeName} items`}>
                  {lines.map((line) => {
                    const updating = action === `line-${line.ref.variant_id}`;
                    const locked = action !== null || frozen;
                    return <li key={line.ref.variant_id} className={`py-5 ${line.available ? "" : "opacity-60"}`}>
                      <div className="flex items-start justify-between gap-4">
                        <div className="min-w-0">
                          <p className="line-clamp-2 text-[15px] font-semibold leading-5">{line.name}</p>
                          <p className="mt-1 truncate text-[13px] text-muted">{[line.pack_size, `${line.unit_price.display} each`].filter(Boolean).join(" · ")}</p>
                          {!line.available && <p className="mt-1 text-xs font-medium text-danger">No longer available</p>}
                        </div>
                        <strong className="shrink-0 text-[15px] tabular-nums">{line.line_total.display}</strong>
                      </div>
                      <div className="mt-3 flex items-center gap-2">
                        <div role="group" aria-label={`Quantity of ${line.name}`} className="flex h-10 items-center rounded-full border border-border-strong bg-surface">
                          <button type="button" onClick={() => void changeQuantity(line, line.quantity - 1)} disabled={locked} aria-label={`Decrease quantity of ${line.name}`} className="grid size-10 place-items-center rounded-full text-ink-secondary transition-[transform,background-color] duration-150 ease-out hover:bg-accent-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus active:scale-[.94] disabled:opacity-40 motion-reduce:active:scale-100"><Minus size={14} weight="bold" /></button>
                          <span className="min-w-7 text-center text-sm font-semibold tabular-nums" aria-live="polite">{updating ? <CircleNotch size={14} className="mx-auto animate-spin motion-reduce:animate-none" aria-label="Updating" /> : line.quantity}</span>
                          <button type="button" onClick={() => void changeQuantity(line, line.quantity + 1)} disabled={locked || !line.available || line.quantity >= MAX_QUANTITY} aria-label={`Increase quantity of ${line.name}`} className="grid size-10 place-items-center rounded-full text-ink-secondary transition-[transform,background-color] duration-150 ease-out hover:bg-accent-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus active:scale-[.94] disabled:opacity-40 motion-reduce:active:scale-100"><Plus size={14} weight="bold" /></button>
                        </div>
                        <button type="button" onClick={() => provider && onSwap(provider, line)} disabled={locked} aria-label={`Swap ${line.name} for another option`} className="flex h-10 items-center gap-1.5 rounded-full px-3 text-[13px] font-semibold text-accent-ink transition-[transform,background-color] duration-150 ease-out hover:bg-accent-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus active:scale-[.97] disabled:opacity-40 motion-reduce:active:scale-100"><ArrowsLeftRight size={14} weight="bold" />Swap</button>
                        <button type="button" onClick={() => void changeQuantity(line, 0)} disabled={locked} aria-label={`Remove ${line.name}`} className="ml-auto grid size-10 place-items-center rounded-full text-subtle transition-[transform,background-color,color] duration-150 ease-out hover:bg-danger-soft hover:text-danger focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus active:scale-[.94] disabled:opacity-40 motion-reduce:active:scale-100"><Trash size={17} /></button>
                      </div>
                    </li>;
                  })}
                </ul>
                <div className="flex items-center justify-between gap-3 border-t border-border py-3">
                  <button type="button" onClick={onSaveAsList} disabled={action !== null} className="-ml-3 flex h-10 items-center gap-1.5 rounded-full px-3 text-[13px] font-semibold text-ink-secondary transition-[transform,background-color] duration-150 ease-out hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus active:scale-[.97] disabled:opacity-40 motion-reduce:active:scale-100"><BookmarkSimple size={16} />Save as list</button>
                  <button type="button" onClick={() => { setClearError(""); setClearOpen(true); }} disabled={!edit.canClear} className="-mr-3 flex h-10 items-center gap-1.5 rounded-full px-3 text-[13px] font-semibold text-danger-hover transition-[transform,background-color] duration-150 ease-out hover:bg-danger-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus active:scale-[.97] disabled:cursor-not-allowed disabled:opacity-40 motion-reduce:active:scale-100"><Trash size={16} />Clear basket</button>
                </div>
              </>}
              <AnimatePresence initial={false}>
                {notice && <motion.p key={notice.text} role="status" aria-live="polite" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }} className={`mb-4 rounded-2xl px-4 py-3 text-sm leading-5 ${notice.error ? "bg-danger-soft text-danger-hover" : "bg-success-soft text-success-strong"}`}>{notice.text}</motion.p>}
              </AnimatePresence>
            </div>

            {!view.empty && <footer className="shrink-0 border-t border-border px-6 pb-[max(1.25rem,env(safe-area-inset-bottom))] pt-4">
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-sm text-muted">Item total</span>
                <strong className="text-lg tabular-nums">{basket?.item_total.display}</strong>
              </div>
              <p className="mt-0.5 text-xs text-muted">{FEES_NOTE}{commerceMode === "live" ? `. Getting it syncs this basket to your ${storeName} cart; nothing is ordered.` : ""}</p>
              <button type="button" onClick={() => void getBill()} disabled={bill.disabled} aria-describedby={bill.reason ? `${titleId}-bill-reason` : undefined} className="mt-4 flex h-[52px] w-full items-center justify-center gap-2 rounded-full bg-accent text-[15px] font-semibold text-on-accent shadow-[0_12px_28px_-16px_rgb(var(--accent-shadow)/.8)] transition-[transform,background-color] duration-150 ease-out hover:bg-accent-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 active:scale-[.98] disabled:cursor-not-allowed disabled:opacity-45 motion-reduce:active:scale-100">
                {action === "bill" ? <><CircleNotch size={18} className="animate-spin motion-reduce:animate-none" />Getting final bill…</> : <><Receipt size={18} />Get final bill</>}
              </button>
              {bill.reason && <p id={`${titleId}-bill-reason`} className="mt-2.5 text-center text-xs leading-5 text-muted">{bill.reason}</p>}
            </footer>}
            {view.empty && <div className="shrink-0 pb-[max(1rem,env(safe-area-inset-bottom))]" />}
          </motion.div>
        </motion.div>}
      </AnimatePresence>,
      document.body,
    )}
    {clearCopy && <ConfirmDialog open={clearOpen} tone="destructive" title={clearCopy.title} message={clearCopy.body} confirmLabel={clearCopy.confirmLabel} busyLabel={clearCopy.busyLabel} busy={action === "clear"} error={clearError || undefined} onConfirm={() => void clearBasket()} onClose={() => setClearOpen(false)} />}
  </>;
}
