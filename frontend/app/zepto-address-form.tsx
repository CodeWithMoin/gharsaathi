"use client";

import { Crosshair, SpinnerGap, WarningCircle } from "@phosphor-icons/react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { useApiRequest } from "./authenticated-api";
import { firstEmptyField, mergeAddressPrefill, type AddressFormValues } from "./address-form-merge.mjs";
import { hasUnsavedChanges } from "./ui/input-dialog-logic.mjs";
import type { AddressPrefill } from "./voice-address-prefill.mjs";

type Address = { id: string; address_line: string; category?: string; tag?: string };
export type ZeptoAddressPrefill = AddressPrefill;
export type ZeptoAddressList = {
  addresses: Address[];
  resolution: { default_address_id?: string | null };
};
/** A prefill from the assistant; a new key merges it into the open form. */
export type ZeptoAddressPrefillRequest = { key: number; values: ZeptoAddressPrefill };
export type ZeptoPrefillResult = { visible: boolean; filled: string[]; kept: string[] };

const ADDRESS_TYPES = ["HOME", "WORK", "OTHER"];
const EMPTY: AddressFormValues = { type: "HOME" };

export function ZeptoAddressForm({
  formId,
  onAdded,
  prefillRequest,
  onPrefillApplied,
  onDirtyChange,
  onBusyChange,
}: {
  formId: string;
  onAdded: (result: ZeptoAddressList) => void;
  prefillRequest?: ZeptoAddressPrefillRequest | null;
  onPrefillApplied?: (result: ZeptoPrefillResult) => void;
  onDirtyChange?: (dirty: boolean) => void;
  onBusyChange?: (busy: boolean) => void;
}) {
  const request = useApiRequest();
  const formRef = useRef<HTMLFormElement | null>(null);
  const [values, setValues] = useState<AddressFormValues>(EMPTY);
  const valuesRef = useRef<AddressFormValues>(EMPTY);
  const touchedRef = useRef(new Set<string>());
  const [fromAssistant, setFromAssistant] = useState(false);
  const [latitude, setLatitude] = useState("");
  const [longitude, setLongitude] = useState("");
  const [locating, setLocating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const callbacksRef = useRef({ onPrefillApplied, onDirtyChange, onBusyChange });
  useEffect(() => { callbacksRef.current = { onPrefillApplied, onDirtyChange, onBusyChange }; });

  const prefillKey = prefillRequest?.key;
  useEffect(() => {
    const incoming = prefillRequest?.values;
    if (prefillKey === undefined || !incoming) return;
    const merged = mergeAddressPrefill(valuesRef.current, incoming, touchedRef.current);
    valuesRef.current = merged.values;
    setValues(merged.values);
    if (Object.keys(incoming).length) setFromAssistant(true);
    const frame = window.requestAnimationFrame(() => {
      const form = formRef.current;
      const visible = Boolean(form && form.isConnected && form.getClientRects().length > 0);
      const empty = firstEmptyField(merged.values);
      const field = empty ? form?.elements.namedItem(empty) : null;
      if (field instanceof HTMLElement && !form?.contains(document.activeElement)) field.focus({ preventScroll: false });
      callbacksRef.current.onPrefillApplied?.({ visible, filled: merged.filled, kept: merged.kept });
    });
    return () => window.cancelAnimationFrame(frame);
    // Only a new request key merges; the values object identity is irrelevant.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillKey]);

  useEffect(() => {
    callbacksRef.current.onDirtyChange?.(hasUnsavedChanges(EMPTY, { ...values, latitude, longitude }));
  }, [values, latitude, longitude]);
  useEffect(() => { callbacksRef.current.onBusyChange?.(saving); }, [saving]);

  function setField(name: keyof AddressFormValues, value: string) {
    touchedRef.current.add(name);
    const next = { ...valuesRef.current, [name]: value };
    valuesRef.current = next;
    setValues(next);
  }

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
      onAdded(await request<ZeptoAddressList>("/api/zepto/addresses", {
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

  const inputClass = "mt-1.5 w-full rounded-xl border border-border-strong bg-white px-3 py-2.5 text-sm text-ink outline-none transition placeholder:font-normal placeholder:italic placeholder:text-subtle focus:border-zepto focus:ring-2 focus:ring-zepto/15";
  const labelClass = "block text-[11px] font-semibold text-ink-secondary";
  const text = (name: keyof AddressFormValues) => ({
    name,
    value: values[name] ?? "",
    onChange: (event: { target: { value: string } }) => setField(name, event.target.value),
  });

  return (
    <form id={formId} ref={formRef} onSubmit={submit} className="space-y-4" noValidate={false}>
      {fromAssistant && <p className="rounded-xl bg-zepto-soft px-3 py-2 text-[11px] font-medium leading-5 text-zepto-strong">Filled only from what you said. Check every field, set the location, and type the contact number yourself.</p>}
      <div className="grid grid-cols-2 gap-3">
        <label className={labelClass}>Type<select {...text("type")} value={values.type && ADDRESS_TYPES.includes(values.type) ? values.type : "HOME"} className={inputClass}><option value="HOME">Home</option><option value="WORK">Work</option><option value="OTHER">Other</option></select></label>
        <label className={labelClass}>Label<input {...text("name")} required maxLength={80} placeholder="e.g. Home or Office" className={inputClass} /></label>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <label className={labelClass}>Flat / house<input {...text("flat_details")} required maxLength={160} placeholder="Flat or house number" className={inputClass} /></label>
        <label className={labelClass}>Building / society<input {...text("building_name")} required maxLength={160} placeholder="Building or society name" className={inputClass} /></label>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <label className={labelClass}>Building type<select name="building_type" defaultValue="BUILDING_TYPE_SOCIETY" className={inputClass}><option value="BUILDING_TYPE_SOCIETY">Society</option><option value="BUILDING_TYPE_HOUSE">House</option><option value="BUILDING_TYPE_OTHERS">Other</option></select></label>
        <label className={labelClass}>Floor <span className="font-normal text-zepto-muted">optional</span><input {...text("floor")} maxLength={40} placeholder="Floor number" className={inputClass} /></label>
      </div>
      <label className={labelClass}>Full formatted address<textarea {...text("formatted_address")} required maxLength={500} rows={2} placeholder="Street, area, city, state and PIN code" className={inputClass} /></label>
      <label className={labelClass}>Area / city / state<input {...text("short_address")} required maxLength={200} placeholder="Area, city and state" className={inputClass} /></label>
      <label className={labelClass}>Nearby landmark <span className="font-normal text-zepto-muted">optional</span><input {...text("landmark")} maxLength={160} placeholder="A place nearby" className={inputClass} /></label>

      <div className="grid grid-cols-2 gap-3">
        <label className={labelClass}>Delivery contact<input {...text("contact_name")} required maxLength={100} autoComplete="name" placeholder="Name" className={inputClass} /></label>
        <label className={labelClass}>Contact number<input {...text("contact_number")} required minLength={7} maxLength={20} inputMode="tel" autoComplete="tel" placeholder="Type your number" className={inputClass} /></label>
      </div>

      <div className="rounded-xl border border-zepto-border bg-white p-3">
        <div className="flex items-center justify-between gap-3"><p className="text-[11px] font-semibold text-zepto-muted">Map location</p><button type="button" onClick={useCurrentLocation} disabled={locating} className="flex items-center gap-1.5 text-[11px] font-semibold text-zepto-strong disabled:opacity-50">{locating ? <SpinnerGap size={14} className="animate-spin" /> : <Crosshair size={14} />} Use current location</button></div>
        <div className="mt-2 grid grid-cols-2 gap-3">
          <label className={labelClass}>Latitude<input name="latitude" value={latitude} onChange={(event) => setLatitude(event.target.value)} required inputMode="decimal" placeholder="Set by location" className={inputClass} /></label>
          <label className={labelClass}>Longitude<input name="longitude" value={longitude} onChange={(event) => setLongitude(event.target.value)} required inputMode="decimal" placeholder="Set by location" className={inputClass} /></label>
        </div>
      </div>

      {error && <p className="flex gap-2 rounded-xl bg-danger-soft px-3 py-2.5 text-xs leading-5 text-danger-hover" role="alert"><WarningCircle size={16} className="mt-0.5 shrink-0" weight="fill" />{error}</p>}
      <p className="text-[10px] leading-4 text-zepto-muted">This saves the address and delivery contact to your connected Zepto account. If saving times out, refresh addresses before trying again—it may already have been saved.</p>
    </form>
  );
}
