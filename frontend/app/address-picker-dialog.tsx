"use client";

import { ArrowClockwise, Check, MapPin, Plus } from "@phosphor-icons/react";
import { useId } from "react";
import { addressLabel, addressSummary, selectedAddress, type SavedAddress } from "./input-flows.mjs";
import { InputDialog } from "./ui/input-dialog";

export type AddressPickerStore = "swiggy" | "zepto";

const STORE = {
  swiggy: { name: "Swiggy Instamart", short: "Swiggy", accent: "accent-[#c6531d]", text: "text-[#c6531d]", button: "bg-[#c6531d] hover:bg-[#b34a19]", ring: "has-[:checked]:border-[#e7b89e] has-[:checked]:bg-[#fff6f0]" },
  zepto: { name: "Zepto", short: "Zepto", accent: "accent-[#7b3fc4]", text: "text-[#7b3fc4]", button: "bg-[#5f2e91] hover:bg-[#51277c]", ring: "has-[:checked]:border-[#cdb4ea] has-[:checked]:bg-[#faf6fe]" },
} as const;

/**
 * "Choose delivery address" popup for one store. Tapping an address saves it
 * right away (same call as before); "Add a new address" opens the address popup on top.
 */
export function AddressPickerDialog({ open, store, commerceMode, addresses, selectedId, refreshing, emptyText, onSelect, onRefresh, onAddNew, onClose }: {
  open: boolean;
  store: AddressPickerStore;
  commerceMode: "demo" | "live";
  addresses: SavedAddress[];
  selectedId: string;
  refreshing: boolean;
  emptyText: string;
  onSelect: (addressId: string) => void;
  onRefresh: () => void;
  onAddNew?: () => void;
  onClose: () => void;
}) {
  const groupName = useId();
  const style = STORE[store];
  const demo = commerceMode === "demo";
  const chosen = selectedAddress(addresses, selectedId);
  const refreshLabel = refreshing ? "Refreshing…" : demo ? "Reset demo" : `Refresh from ${style.short}`;

  return <InputDialog
    open={open}
    title="Choose delivery address"
    description={demo ? `${style.name} · local demo addresses only` : `${style.name} · saved addresses. Refresh fetches them again from ${style.short}.`}
    closeLabel="Close choose delivery address"
    widthClassName="sm:max-w-md"
    onClose={onClose}
    footer={({ requestClose }) => <div className="flex items-center gap-3">
      <button type="button" onClick={onRefresh} disabled={refreshing} className={`flex items-center gap-1.5 rounded-full px-3 py-3 text-sm font-semibold ${style.text} transition hover:bg-[#f6eee8] disabled:cursor-not-allowed disabled:opacity-45`}><ArrowClockwise size={16} className={refreshing ? "animate-spin" : ""} />{refreshLabel}</button>
      <button type="button" onClick={() => requestClose("done")} disabled={!chosen} className={`ml-auto rounded-full px-5 py-3 text-sm font-semibold text-white transition disabled:opacity-40 ${style.button}`}>Use this address</button>
    </div>}
  >
    {refreshing && !addresses.length
      ? <div className="space-y-2" role="status" aria-label="Loading addresses"><div className="h-16 animate-pulse rounded-2xl bg-[#eee2d8]" /><div className="h-16 animate-pulse rounded-2xl bg-[#eee2d8]" /></div>
      : addresses.length
        ? <fieldset>
          <legend className="sr-only">Saved {style.name} addresses</legend>
          <div className="space-y-2">
            {addresses.map((address) => {
              const isSelected = address.id === selectedId;
              return <label key={address.id} className={`flex cursor-pointer items-start gap-3 rounded-2xl border border-[#eadbd0] bg-white px-3.5 py-3 transition hover:bg-[#fffaf6] ${style.ring}`}>
                <input type="radio" name={groupName} checked={isSelected} onChange={() => onSelect(address.id)} className={`mt-1 ${style.accent}`} />
                <span className="min-w-0 flex-1"><strong className="block text-sm">{addressLabel(address)}</strong>{address.address_line && <small className="mt-1 block break-words leading-relaxed text-[#806f65]">{address.address_line}</small>}</span>
                {isSelected && <Check size={17} className={`mt-0.5 shrink-0 ${style.text}`} aria-hidden="true" />}
              </label>;
            })}
          </div>
        </fieldset>
        : <p className="rounded-2xl bg-[#f7ede5] px-4 py-3 text-xs leading-5 text-[#806f65]">{emptyText}</p>}
    {onAddNew && <button type="button" onClick={onAddNew} className={`mt-4 flex min-h-11 items-center gap-1.5 text-sm font-semibold ${style.text}`}><Plus size={15} /> Add a new address</button>}
  </InputDialog>;
}

/** Setup-sheet row: the store's chosen address and a button that opens the picker. */
export function StoreAddressRow({ store, commerceMode, addresses, selectedId, loading, onChoose }: {
  store: AddressPickerStore;
  commerceMode: "demo" | "live";
  addresses: SavedAddress[];
  selectedId: string;
  loading: boolean;
  onChoose: () => void;
}) {
  const style = STORE[store];
  const summary = addressSummary(addresses, selectedId);
  const heading = commerceMode === "demo" ? `${style.short} demo address` : `${style.short} delivery address`;
  return <div className="flex items-center gap-3 rounded-2xl border border-[#e7d8cd] bg-[#fffaf5] px-4 py-3">
    <MapPin size={20} weight="duotone" className={`shrink-0 ${style.text}`} />
    <div className="min-w-0 flex-1">
      <h3 className="text-[11px] font-semibold uppercase tracking-[.12em] text-[#9b8172]">{heading}</h3>
      {loading && !addresses.length
        ? <span className="mt-1 block h-4 w-32 animate-pulse rounded bg-[#eee2d8]" aria-label="Loading addresses" />
        : <><p className={`mt-0.5 truncate text-sm font-semibold ${summary.chosen ? "" : "text-[#806f65]"}`}>{summary.title}</p>{summary.detail && <p className="truncate text-xs text-[#806f65]">{summary.detail}</p>}</>}
    </div>
    <button type="button" onClick={onChoose} className={`shrink-0 rounded-full border border-[#e4cabc] bg-white px-3.5 py-2 text-xs font-semibold ${style.text} transition hover:bg-[#fff5ed]`}>{summary.chosen ? "Change" : "Choose"}</button>
  </div>;
}
