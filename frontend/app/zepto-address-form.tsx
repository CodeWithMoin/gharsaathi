"use client";

import { Crosshair, MapPin, SpinnerGap, WarningCircle, X } from "@phosphor-icons/react";
import { FormEvent, useEffect, useRef, useState } from "react";
import { useApiRequest } from "./authenticated-api";
import type { AddressPrefill } from "./voice-address-prefill.mjs";

type Address = { id: string; address_line: string; category?: string; tag?: string };
export type ZeptoAddressPrefill = AddressPrefill;
type AddressList = {
  addresses: Address[];
  resolution: { default_address_id?: string | null };
};

const ADDRESS_TYPES = ["HOME", "WORK", "OTHER"];

export function ZeptoAddressForm({
  onAdded,
  onCancel,
  prefill,
}: {
  onAdded: (result: AddressList) => void;
  onCancel: () => void;
  prefill?: ZeptoAddressPrefill;
}) {
  const request = useApiRequest();
  const formRef = useRef<HTMLFormElement | null>(null);
  const initial = prefill ?? {};
  const hasPrefill = prefill !== undefined;

  useEffect(() => {
    if (hasPrefill) formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [hasPrefill]);
  const [latitude, setLatitude] = useState("");
  const [longitude, setLongitude] = useState("");
  const [locating, setLocating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function useCurrentLocation() {
    if (!navigator.geolocation) {
      setError("This browser cannot provide your location. Enter coordinates manually.");
      return;
    }
    setLocating(true);
    setError(null);
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        setLatitude(coords.latitude.toFixed(6));
        setLongitude(coords.longitude.toFixed(6));
        setLocating(false);
      },
      () => {
        setError("Location permission was not available. Enter coordinates manually.");
        setLocating(false);
      },
      { enableHighAccuracy: true, timeout: 12_000, maximumAge: 60_000 },
    );
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    const form = new FormData(event.currentTarget);
    const body = {
      type: form.get("type"),
      name: form.get("name"),
      flat_details: form.get("flat_details"),
      building_name: form.get("building_name"),
      building_type: form.get("building_type"),
      floor: form.get("floor") || null,
      landmark: form.get("landmark") || null,
      latitude: Number(latitude),
      longitude: Number(longitude),
      formatted_address: form.get("formatted_address"),
      short_address: form.get("short_address"),
      contact_name: form.get("contact_name"),
      contact_number: form.get("contact_number"),
    };
    if (!latitude || !longitude || !Number.isFinite(body.latitude) || !Number.isFinite(body.longitude)) {
      setError("Use current location or enter valid latitude and longitude.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      onAdded(await request<AddressList>("/api/zepto/addresses", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save this address to Zepto.");
    } finally {
      setSaving(false);
    }
  }

  const inputClass = "mt-1.5 w-full rounded-xl border border-[#ddcec3] bg-white px-3 py-2.5 text-sm outline-none transition focus:border-[#7b3fc4] focus:ring-2 focus:ring-[#7b3fc4]/15";
  const labelClass = "block text-[11px] font-semibold text-[#624f44]";

  return (
    <form ref={formRef} onSubmit={submit} className="mt-4 space-y-4 rounded-2xl border border-[#dfd0eb] bg-[#fbf7ff] p-4">
      <div className="flex items-start gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-[#eee2f8] text-[#6f359f]"><MapPin size={18} weight="fill" /></span>
        <div><h4 className="text-sm font-semibold text-[#3e2b49]">Add delivery address</h4><p className="mt-1 text-[11px] leading-5 text-[#786a80]">This saves the address and delivery contact to your connected Zepto account.</p>{hasPrefill && <p className="mt-1.5 text-[11px] font-medium leading-5 text-[#6f359f]">Filled from what you said—please check every field, set the location, and add the contact number.</p>}</div>
        <button type="button" onClick={onCancel} className="ml-auto grid size-8 place-items-center rounded-full text-[#7a687f] hover:bg-[#eee3f4]" aria-label="Close address form"><X size={15} /></button>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <label className={labelClass}>Type<select name="type" defaultValue={initial.type && ADDRESS_TYPES.includes(initial.type) ? initial.type : "HOME"} className={inputClass}><option value="HOME">Home</option><option value="WORK">Work</option><option value="OTHER">Other</option></select></label>
        <label className={labelClass}>Label<input name="name" defaultValue={initial.name} required maxLength={80} placeholder="Home" className={inputClass} /></label>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <label className={labelClass}>Flat / house<input name="flat_details" defaultValue={initial.flat_details} required maxLength={160} placeholder="Flat 4B" className={inputClass} /></label>
        <label className={labelClass}>Building / society<input name="building_name" defaultValue={initial.building_name} required maxLength={160} placeholder="Lake View Apartments" className={inputClass} /></label>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <label className={labelClass}>Building type<select name="building_type" defaultValue="BUILDING_TYPE_SOCIETY" className={inputClass}><option value="BUILDING_TYPE_SOCIETY">Society</option><option value="BUILDING_TYPE_HOUSE">House</option><option value="BUILDING_TYPE_OTHERS">Other</option></select></label>
        <label className={labelClass}>Floor <span className="font-normal text-[#9a8c9f]">optional</span><input name="floor" defaultValue={initial.floor} maxLength={40} placeholder="4" className={inputClass} /></label>
      </div>
      <label className={labelClass}>Full formatted address<textarea name="formatted_address" defaultValue={initial.formatted_address} required maxLength={500} rows={2} placeholder="Street, area, city, state, PIN code" className={inputClass} /></label>
      <label className={labelClass}>Area / city / state<input name="short_address" defaultValue={initial.short_address} required maxLength={200} placeholder="Indiranagar, Bengaluru, Karnataka" className={inputClass} /></label>
      <label className={labelClass}>Nearby landmark <span className="font-normal text-[#9a8c9f]">optional</span><input name="landmark" defaultValue={initial.landmark} maxLength={160} placeholder="Near metro station" className={inputClass} /></label>

      <div className="grid grid-cols-2 gap-3">
        <label className={labelClass}>Delivery contact<input name="contact_name" defaultValue={initial.contact_name} required maxLength={100} autoComplete="name" className={inputClass} /></label>
        <label className={labelClass}>Contact number<input name="contact_number" required minLength={7} maxLength={20} inputMode="tel" autoComplete="tel" className={inputClass} /></label>
      </div>

      <div className="rounded-xl border border-[#e1d5e8] bg-white p-3">
        <div className="flex items-center justify-between gap-3"><p className="text-[11px] font-semibold text-[#624f6d]">Map coordinates</p><button type="button" onClick={useCurrentLocation} disabled={locating} className="flex items-center gap-1.5 text-[11px] font-semibold text-[#6f359f] disabled:opacity-50">{locating ? <SpinnerGap size={14} className="animate-spin" /> : <Crosshair size={14} />} Use current location</button></div>
        <div className="mt-2 grid grid-cols-2 gap-3">
          <label className={labelClass}>Latitude<input value={latitude} onChange={(event) => setLatitude(event.target.value)} required inputMode="decimal" placeholder="12.971599" className={inputClass} /></label>
          <label className={labelClass}>Longitude<input value={longitude} onChange={(event) => setLongitude(event.target.value)} required inputMode="decimal" placeholder="77.594566" className={inputClass} /></label>
        </div>
      </div>

      {error && <p className="flex gap-2 rounded-xl bg-[#fcedea] px-3 py-2.5 text-xs leading-5 text-[#974237]" role="alert"><WarningCircle size={16} className="mt-0.5 shrink-0" weight="fill" />{error}</p>}
      <button type="submit" disabled={saving || locating} className="flex w-full items-center justify-center gap-2 rounded-full bg-[#6f359f] px-4 py-3 text-sm font-semibold text-white transition hover:bg-[#5d2c86] disabled:opacity-45">{saving ? <><SpinnerGap size={17} className="animate-spin" /> Saving to Zepto…</> : "Save address to Zepto"}</button>
      <p className="text-center text-[10px] leading-4 text-[#8c7e91]">If the request times out, refresh addresses before trying again—the address may already have been saved.</p>
    </form>
  );
}
