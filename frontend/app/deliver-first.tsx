"use client";

import { CaretRight, CircleNotch, MapPin, Plus, ArrowClockwise } from "@phosphor-icons/react";
import { addressLabel, type AddressGate } from "./home-header.mjs";

/**
 * "Where should we deliver?": shown on the home screen before the greeting and composer
 * when a store is connected but no valid delivery address is chosen. One tap on an
 * address saves it and the chat continues. Deliberately inline (like the onboarding
 * pages), not a popup: it is the home screen's first step, not an interruption.
 */
export function DeliverFirst({ gate, commerceMode, refreshing, onSelect, onAdd, onRefresh }: {
  gate: AddressGate;
  commerceMode: "demo" | "live";
  refreshing: boolean;
  onSelect: (addressId: string) => void;
  /** Zepto (live) only: opens the Add delivery address popup. */
  onAdd?: () => void;
  onRefresh: () => void;
}) {
  const ring = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus";
  return <section aria-labelledby="deliver-first-title" className="mr-0 max-w-[620px]">
    <h2 id="deliver-first-title" className="font-display text-2xl font-semibold tracking-[-.04em] text-ink">Where should we deliver?</h2>
    <p className="mt-1.5 text-sm leading-6 text-muted">Choose a saved {gate.storeName} address. You can change it any time from the top of the screen.</p>
    {gate.addresses.length > 0
      ? <ul className="mt-5 overflow-hidden rounded-[22px] border border-border bg-surface">
        {gate.addresses.map((address, index) => <li key={address.id} className={index ? "border-t border-border" : ""}>
          <button type="button" onClick={() => onSelect(address.id)} className={`flex min-h-[68px] w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-canvas active:bg-surface-muted focus-visible:relative focus-visible:z-10 focus-visible:ring-inset ${ring}`}>
            <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent-hover"><MapPin size={20} weight="duotone" /></span>
            <span className="min-w-0 flex-1"><span className="block truncate text-[15px] font-semibold text-ink">{addressLabel(address)}</span><span className="mt-0.5 line-clamp-2 text-xs leading-4 text-muted">{address.address_line}</span></span>
            <CaretRight size={17} weight="bold" className="shrink-0 text-subtle" aria-hidden="true" />
          </button>
        </li>)}
      </ul>
      : <p className="mt-5 rounded-[22px] border border-dashed border-accent-border bg-surface px-4 py-5 text-sm leading-6 text-muted">{refreshing ? "Loading your saved addresses…" : `No saved ${gate.storeName} address yet.${gate.canAdd ? " Add one below." : ` Add one in the ${gate.storeName} app, then refresh.`}`}</p>}
    <div className="mt-3 flex flex-wrap gap-2">
      {gate.canAdd && commerceMode === "live" && onAdd && <button type="button" onClick={onAdd} className={`flex min-h-11 items-center gap-1.5 rounded-full bg-accent px-4 text-sm font-semibold text-on-accent transition-transform active:scale-[.97] motion-reduce:active:scale-100 ${ring}`}><Plus size={16} weight="bold" />Add address</button>}
      <button type="button" onClick={onRefresh} disabled={refreshing} className={`flex min-h-11 items-center gap-1.5 rounded-full border border-accent-border bg-surface px-4 text-sm font-semibold text-ink-secondary disabled:opacity-55 ${ring}`}>{refreshing ? <CircleNotch size={16} className="animate-spin" /> : <ArrowClockwise size={16} weight="bold" />}{refreshing ? "Refreshing…" : `Refresh from ${gate.storeName}`}</button>
    </div>
  </section>;
}
