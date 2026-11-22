"use client";

import { CircleNotch, MapPin, NotePencil, PhoneCall, Plus, ShieldCheck, Trash, X } from "@phosphor-icons/react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ApiError, useApiRequest } from "./authenticated-api";
import {
  DIRECTIONS_PATH,
  GATE_NOTE_MAX,
  HANDOVER_CHIPS,
  HANDOVER_MAX,
  LANDMARK_MAX,
  LINE_PATH,
  MAX_STEPS,
  STEP_MAX,
  addStep,
  deliveryLineView,
  directionsDirty,
  directionsDraft,
  directionsError,
  directionsPayload,
  pickHandover,
  removeStep,
  stepsSummary,
  updateStep,
  type DeliveryLine,
  type DeliveryProvider,
  type DirectionsDraft,
  type InboundDeliveryConfig,
  type SavedDirections,
  type SelectedDeliveryAddress,
} from "./delivery-line.mjs";
import { ConfirmDialog } from "./ui/confirm-dialog";
import { InputDialog } from "./ui/input-dialog";

const ring = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus";
const POLL_MS = 2_000;
const MAX_POLLS = 15;
const STORE_NAMES: Record<DeliveryProvider, string> = { swiggy: "Swiggy", zepto: "Zepto" };
const TONES = {
  neutral: "bg-surface-muted text-muted",
  success: "bg-success-soft text-success",
  warning: "bg-warning-soft text-warning",
  danger: "bg-danger-soft text-danger",
} as const;

export type DeliveryLinePanelProps = {
  provider: DeliveryProvider;
  /** The store address selected in the app; the line always uses this one. */
  address: SelectedDeliveryAddress | null;
  recipientName: string;
  onChangeAddress?: () => void;
  /** Changes when tracking starts or the order status changes, to re-read the line. */
  refreshKey?: string;
};

function errorText(reason: unknown, fallback: string) {
  return reason instanceof Error && reason.message !== "Request failed." ? reason.message : fallback;
}

/** Share the selected address and saved directions with the inbound delivery line. Never places a call. */
export function DeliveryLinePanel({ provider, address, recipientName, onChangeAddress, refreshKey }: DeliveryLinePanelProps) {
  const request = useApiRequest();
  const nameId = useId();
  const autoLabelId = useId();
  const [config, setConfig] = useState<InboundDeliveryConfig | null>(null);
  const [line, setLine] = useState<DeliveryLine | null>(null);
  const [directions, setDirections] = useState<SavedDirections | null>(null);
  const [directionsLoading, setDirectionsLoading] = useState(false);
  const [name, setName] = useState(recipientName);
  const [nameDraft, setNameDraft] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState<"" | "share" | "clear" | "auto" | "name" | "delete">("");
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [confirmAuto, setConfirmAuto] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const store = STORE_NAMES[provider];
  const addressId = address?.id ?? null;

  useEffect(() => { setName(recipientName); }, [recipientName]);

  const loadLine = useCallback(async () => {
    try {
      setLine(await request<DeliveryLine>(LINE_PATH));
    } catch {
      // The last known state stays on screen; sharing still re-checks on the server.
    }
  }, [request]);

  useEffect(() => {
    let active = true;
    void request<InboundDeliveryConfig>("/api/inbound-delivery/config")
      .then((next) => { if (active) setConfig(next); })
      .catch(() => { if (active) setConfig({ enabled: false, reason: "not_configured", inbound_phone_number: null, approved_caller_hint: null, route_directions_enabled: false }); });
    return () => { active = false; };
  }, [request]);

  useEffect(() => {
    if (config?.enabled) void loadLine();
  }, [config?.enabled, loadLine, refreshKey, addressId]);

  useEffect(() => {
    setConfirmed(false);
    setError("");
    setDirections(null);
    if (!addressId || !config?.enabled) return;
    let active = true;
    setDirectionsLoading(true);
    const query = new URLSearchParams({ provider, address_id: addressId });
    void request<SavedDirections | null>(`${DIRECTIONS_PATH}?${query}`)
      .then((saved) => { if (active) setDirections(saved); })
      .catch(() => { if (active) setError("Couldn't load the saved directions for this address."); })
      .finally(() => { if (active) setDirectionsLoading(false); });
    return () => { active = false; };
  }, [addressId, config?.enabled, provider, request]);

  // Follow a share or clear until Sarvam finishes; also catch a clear the server
  // started after an address change.
  const pending = line && (line.phase === "processing" || line.phase === "clearing" || (line.phase === "ready" && line.provider === provider && line.address_id !== addressId));
  const cohortId = line?.cohort_id ?? null;
  const phase = line?.phase;
  useEffect(() => {
    if (!pending) return;
    let attempts = 0;
    const interval = window.setInterval(() => {
      attempts += 1;
      if (attempts > MAX_POLLS) {
        window.clearInterval(interval);
        return;
      }
      const path = cohortId && (phase === "processing" || phase === "clearing") ? `/api/inbound-delivery/contexts/${encodeURIComponent(cohortId)}` : LINE_PATH;
      void request<DeliveryLine>(path).then(setLine).catch(() => window.clearInterval(interval));
    }, POLL_MS);
    return () => window.clearInterval(interval);
  }, [cohortId, pending, phase, request]);

  const view = deliveryLineView({ config, address, recipientName: name, directions, line, store });

  async function share() {
    if (!addressId || !confirmed || busy) return;
    setBusy("share");
    setError("");
    try {
      setLine(await request<DeliveryLine>("/api/inbound-delivery/contexts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, address_id: addressId, confirmed: true }),
      }));
      setConfirmed(false);
    } catch (reason) {
      setError(errorText(reason, "Couldn't share with the delivery line."));
      void loadLine();
    } finally {
      setBusy("");
    }
  }

  async function clear() {
    if (busy) return;
    setBusy("clear");
    setError("");
    try {
      setLine(await request<DeliveryLine>(LINE_PATH, { method: "DELETE" }));
    } catch (reason) {
      setError(errorText(reason, "Couldn't clear the delivery line."));
    } finally {
      setBusy("");
    }
  }

  async function setAutoShare(on: boolean) {
    setBusy("auto");
    setError("");
    try {
      setLine(await request<DeliveryLine>(`${LINE_PATH}/settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(on ? { auto_share: true, confirmed: true } : { auto_share: false }),
      }));
      setConfirmAuto(false);
    } catch (reason) {
      setError(errorText(reason, "Couldn't change automatic sharing."));
    } finally {
      setBusy("");
    }
  }

  async function saveName() {
    const next = (nameDraft ?? "").replace(/\s+/g, " ").trim();
    if (next.length < 2) { setError("Add a name of at least 2 letters."); return; }
    setBusy("name");
    setError("");
    try {
      const saved = await request<{ first_name: string | null }>("/api/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ first_name: next }),
      });
      setName(saved.first_name ?? next);
      setNameDraft(null);
      setConfirmed(false);
    } catch (reason) {
      setError(reason instanceof ApiError && reason.status === 422 ? "Use only letters, spaces, apostrophes, full stops and hyphens." : errorText(reason, "Couldn't save the name."));
    } finally {
      setBusy("");
    }
  }

  async function saveDirections(draft: DirectionsDraft) {
    if (!addressId) return;
    const saved = await request<SavedDirections>(DIRECTIONS_PATH, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(directionsPayload(draft, provider, addressId)),
    });
    setDirections(saved);
    setConfirmed(false);
    setEditing(false);
  }

  async function deleteDirections() {
    if (!addressId) return;
    setBusy("delete");
    try {
      const query = new URLSearchParams({ provider, address_id: addressId });
      await request(`${DIRECTIONS_PATH}?${query}`, { method: "DELETE" });
      setDirections(null);
      setConfirmed(false);
      setConfirmDelete(false);
    } catch (reason) {
      setError(errorText(reason, "Couldn't delete the directions."));
    } finally {
      setBusy("");
    }
  }

  const steps = stepsSummary(directions);
  const shared = line && line.provider === provider && line.address_id === addressId && ["ready", "processing", "unknown"].includes(line.phase);
  const clearable = line && ["processing", "ready", "unknown", "rejected", "failed", "clear_failed"].includes(line.phase);

  return (
    <section className="mt-4 rounded-2xl border border-border bg-surface p-3.5" aria-labelledby="delivery-line-title">
      <div className="flex items-start gap-2.5">
        <span className="grid size-8 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent-ink"><PhoneCall size={17} weight="fill" /></span>
        <div className="min-w-0 flex-1">
          <h3 id="delivery-line-title" className="text-xs font-semibold text-ink">Delivery line</h3>
          <p className="mt-0.5 text-[11px] leading-4 text-muted">Lets the AI guide the approved delivery caller to your door. This does not place a call.</p>
        </div>
      </div>

      {view.kind === "loading" ? (
        <div className="mt-3 h-16 animate-pulse rounded-xl bg-surface-muted" aria-label="Checking the delivery line" />
      ) : view.kind === "off" ? (
        <p className="mt-3 rounded-xl bg-surface-muted px-3 py-2 text-[11px] leading-4 text-muted">{view.message}</p>
      ) : (
        <div className="mt-3 space-y-2.5">
          <div className="rounded-xl border border-border bg-white px-3 py-2.5">
            <div className="flex items-start gap-2">
              <MapPin size={15} weight="fill" className="mt-0.5 shrink-0 text-accent-ink" />
              <div className="min-w-0 flex-1">
                <p className="text-[10px] font-semibold text-ink-secondary">{store} delivery address{address?.label ? ` · ${address.label}` : ""}</p>
                <p className="mt-0.5 text-xs leading-5 text-ink">{address?.text ?? "No address selected yet."}</p>
              </div>
              {onChangeAddress && <button type="button" onClick={onChangeAddress} className={`shrink-0 rounded-full border border-border-strong px-2.5 py-1 text-[10px] font-semibold text-ink-secondary transition hover:bg-canvas ${ring}`}>{address ? "Change address" : "Choose address"}</button>}
            </div>
          </div>

          <div className="rounded-xl border border-border bg-white px-3 py-2.5">
            {nameDraft === null ? (
              <div className="flex items-center gap-2">
                <p className="min-w-0 flex-1 text-xs text-ink"><span className="text-[10px] font-semibold text-ink-secondary">Recipient </span>{name.trim() || "Not set"}</p>
                <button type="button" onClick={() => setNameDraft(name)} className={`shrink-0 rounded-full px-2.5 py-1 text-[10px] font-semibold text-ink-secondary transition hover:bg-canvas ${ring}`}>Edit</button>
              </div>
            ) : (
              <form onSubmit={(event) => { event.preventDefault(); void saveName(); }} className="flex items-end gap-2">
                <label htmlFor={nameId} className="min-w-0 flex-1 text-[10px] font-semibold text-ink-secondary">Recipient
                  <input id={nameId} value={nameDraft} onChange={(event) => setNameDraft(event.target.value)} maxLength={40} autoComplete="given-name" className="mt-1 w-full rounded-lg border border-border-strong bg-white px-2.5 py-1.5 text-xs font-normal text-ink outline-none focus:border-accent" />
                </label>
                <button type="button" onClick={() => setNameDraft(null)} aria-label="Cancel name change" className={`grid size-8 shrink-0 place-items-center rounded-full text-muted hover:bg-canvas ${ring}`}><X size={14} /></button>
                <button type="submit" disabled={busy === "name"} className={`shrink-0 rounded-full bg-inverse px-3 py-1.5 text-[11px] font-semibold text-on-inverse disabled:opacity-50 ${ring}`}>{busy === "name" ? "Saving…" : "Save"}</button>
              </form>
            )}
            {nameDraft !== null && <p className="mt-1 text-[10px] leading-4 text-muted">Saved to your profile and used next time.</p>}
          </div>

          <div className="rounded-xl border border-border bg-white px-3 py-2.5">
            <div className="flex items-center gap-2">
              <p className="min-w-0 flex-1 text-[10px] font-semibold text-ink-secondary">Directions for this address</p>
              {address && <button type="button" disabled={directionsLoading} onClick={() => setEditing(true)} className={`flex shrink-0 items-center gap-1 rounded-full px-2.5 py-1 text-[10px] font-semibold text-ink-secondary transition hover:bg-canvas disabled:opacity-50 ${ring}`}><NotePencil size={12} />{directions ? "Edit" : "Add directions"}</button>}
            </div>
            {directionsLoading ? <div className="mt-1.5 h-8 animate-pulse rounded-lg bg-surface-muted" /> : directions ? (
              <div className="mt-1 space-y-1 text-xs leading-5 text-ink">
                <p><span className="text-muted">Landmark: </span>{directions.landmark}</p>
                {steps.length > 0 && <ol className="space-y-0.5 text-[11px] leading-4 text-ink-secondary">{steps.map((step) => <li key={step}>{step}</li>)}</ol>}
                {directions.gate_note && <p className="text-[11px] text-ink-secondary"><span className="text-muted">Gate: </span>{directions.gate_note}</p>}
                {directions.handover_instructions && <p className="text-[11px] text-ink-secondary"><span className="text-muted">Handover: </span>{directions.handover_instructions}</p>}
                {!config?.route_directions_enabled && (steps.length > 0 || directions.handover_instructions) && <p className="text-[10px] leading-4 text-subtle">Steps and handover are saved; this server shares only the address and landmark for now.</p>}
              </div>
            ) : <p className="mt-1 text-[11px] leading-4 text-muted">{address ? "None saved yet. Add a landmark and a few steps once; they're reused for this address." : "Choose an address first."}</p>}
          </div>

          {view.message && <p role={view.tone === "danger" ? "alert" : "status"} className={`flex items-start gap-1.5 rounded-xl px-3 py-2 text-[11px] leading-4 ${TONES[view.tone ?? "neutral"]}`}>{view.kind === "ready" && <ShieldCheck size={14} weight="fill" className="mt-0.5 shrink-0" />}{view.message}</p>}
          {line?.error && (view.kind === "clear_failed" || view.kind === "unknown") && <p className="px-0.5 text-[10px] leading-4 text-muted">{line.error}</p>}
          {error && <p role="alert" className="rounded-xl bg-danger-soft px-3 py-2 text-[11px] leading-4 text-danger">{error}</p>}

          {!shared && view.canShare && (
            <>
              <label className="flex items-start gap-2 rounded-xl bg-white px-2.5 py-2 text-[10px] leading-4 text-ink-secondary">
                <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} className="mt-0.5 accent-[rgb(var(--accent))]" />
                <span>I approve sharing this name, address and directions with the approved caller {config?.approved_caller_hint ?? ""} after they confirm the delivery.</span>
              </label>
              <button type="button" onClick={() => void share()} disabled={!confirmed || busy !== ""} className={`flex w-full items-center justify-center gap-1.5 rounded-full bg-inverse px-4 py-2.5 text-xs font-semibold text-on-inverse transition active:scale-[.98] disabled:opacity-45 ${ring}`}>{busy === "share" ? <CircleNotch size={14} className="animate-spin" /> : <PhoneCall size={14} />}{busy === "share" ? "Sharing…" : "Share with delivery line"}</button>
            </>
          )}
          {clearable && <button type="button" onClick={() => void clear()} disabled={busy !== ""} className={`flex w-full items-center justify-center gap-1.5 rounded-full border border-border-strong px-4 py-2.5 text-xs font-semibold text-ink-secondary transition hover:bg-canvas disabled:opacity-50 ${ring}`}>{busy === "clear" && <CircleNotch size={14} className="animate-spin" />}{busy === "clear" ? "Clearing…" : "Clear delivery line"}</button>}

          <div className="flex items-center gap-3 rounded-xl border border-border px-3 py-2.5">
            <div className="min-w-0 flex-1">
              <p id={autoLabelId} className="text-[11px] font-semibold text-ink-secondary">Share automatically when an order is on its way</p>
              <p className="mt-0.5 text-[10px] leading-4 text-muted">Shares when tracking starts and lets you know. Cleared when the order is delivered or cancelled.</p>
            </div>
            <button type="button" role="switch" aria-checked={Boolean(line?.auto_share)} aria-labelledby={autoLabelId} disabled={!line || busy !== ""} onClick={() => (line?.auto_share ? void setAutoShare(false) : setConfirmAuto(true))} className={`relative h-7 w-12 shrink-0 rounded-full transition disabled:opacity-55 ${line?.auto_share ? "bg-accent" : "bg-border-strong"} ${ring} focus-visible:ring-offset-2`}>
              <span className={`absolute top-0.5 grid size-6 place-items-center rounded-full bg-white shadow transition-[left] ${line?.auto_share ? "left-[22px]" : "left-0.5"}`}>{busy === "auto" && <CircleNotch size={12} className="animate-spin text-accent-ink" />}</span>
            </button>
          </div>
        </div>
      )}

      <DirectionsDialog
        open={editing && Boolean(address)}
        initial={directionsDraft(directions)}
        canDelete={Boolean(directions)}
        onDelete={() => { setEditing(false); setConfirmDelete(true); }}
        onClose={() => setEditing(false)}
        onSave={saveDirections}
      />
      <ConfirmDialog
        open={confirmAuto}
        title="Share automatically?"
        message={<>When tracking starts for an order, GharSaathi will share your selected address, name and saved directions with the approved caller {config?.approved_caller_hint ?? ""}, and let you know. It&apos;s cleared when the order is delivered or cancelled.</>}
        confirmLabel="Turn on"
        busyLabel="Turning on…"
        busy={busy === "auto"}
        onClose={() => setConfirmAuto(false)}
        onConfirm={() => void setAutoShare(true)}
      />
      <ConfirmDialog
        open={confirmDelete}
        tone="destructive"
        title="Delete these directions?"
        message="The landmark, steps and handover note for this address will be deleted."
        confirmLabel="Delete"
        busyLabel="Deleting…"
        busy={busy === "delete"}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => void deleteDirections()}
      />
    </section>
  );
}

function DirectionsDialog({ open, initial, canDelete, onDelete, onClose, onSave }: { open: boolean; initial: DirectionsDraft; canDelete: boolean; onDelete: () => void; onClose: () => void; onSave: (draft: DirectionsDraft) => Promise<void> }) {
  const baseId = useId();
  const formId = `${baseId}-form`;
  const landmarkRef = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState<DirectionsDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (open && !draft) setDraft(initial);
  else if (!open && draft && !busy) setDraft(null);
  const values = draft ?? initial;
  const dirty = open && draft !== null && directionsDirty(draft, initial);
  const change = (next: DirectionsDraft) => { setError(""); setDraft(next); };

  async function submit() {
    if (busy) return;
    const problem = directionsError(values);
    if (problem) { setError(problem); return; }
    setBusy(true);
    try {
      await onSave(values);
    } catch (cause) {
      setError(errorText(cause, "Couldn't save. Check the directions and try again."));
    } finally {
      setBusy(false);
    }
  }

  const field = `mt-1.5 w-full rounded-xl border border-border-strong bg-white px-3 text-sm outline-none focus:border-accent focus-visible:ring-2 focus-visible:ring-focus/30`;
  const handover = values.handover_instructions.replace(/\s+/g, " ").trim();
  return <InputDialog
    open={open}
    title="Directions for this address"
    description="Shared only with the approved delivery caller, and only after you approve. Don't add OTPs, PINs or payment details."
    dirty={dirty}
    busy={busy}
    discardMessage="Discard your direction changes?"
    closeLabel="Close directions"
    widthClassName="sm:max-w-md"
    initialFocusRef={landmarkRef}
    onClose={() => { setError(""); onClose(); }}
    footer={({ requestClose }) => <div className="flex items-center gap-3">
      {canDelete && <button type="button" disabled={busy} onClick={onDelete} aria-label="Delete directions" className="grid size-11 place-items-center rounded-full text-danger transition hover:bg-danger-soft disabled:opacity-40"><Trash size={16} /></button>}
      <button type="button" disabled={busy} onClick={() => requestClose("cancel")} className="rounded-full px-4 py-3 text-sm font-semibold text-ink-secondary transition hover:bg-surface-strong disabled:opacity-40">Cancel</button>
      <button type="submit" form={formId} disabled={busy} className="ml-auto flex flex-1 items-center justify-center gap-1.5 rounded-full bg-inverse px-5 py-3 text-sm font-semibold text-on-inverse disabled:opacity-50 sm:flex-none">{busy && <CircleNotch size={15} className="animate-spin" />}{busy ? "Saving…" : "Save"}</button>
    </div>}
  >
    <form id={formId} onSubmit={(event) => { event.preventDefault(); void submit(); }} className="space-y-5">
      <div>
        <label htmlFor={`${baseId}-landmark`} className="block text-xs font-semibold text-ink-secondary">Landmark</label>
        <input ref={landmarkRef} id={`${baseId}-landmark`} value={values.landmark} maxLength={LANDMARK_MAX} disabled={busy} onChange={(event) => change({ ...values, landmark: event.target.value })} placeholder="e.g. Opposite Shiv mandir" className={`${field} min-h-11`} />
      </div>
      <fieldset>
        <legend className="text-xs font-semibold text-ink-secondary">Step-by-step route</legend>
        <p className="mt-0.5 text-[11px] leading-4 text-muted">Short steps, up to {MAX_STEPS}.</p>
        <ol className="mt-2 space-y-2">
          {values.steps.map((step, index) => <li key={index} className="flex items-center gap-2">
            <span className="w-4 shrink-0 text-right text-xs font-semibold text-muted">{index + 1}.</span>
            <input aria-label={`Step ${index + 1}`} value={step} maxLength={STEP_MAX} disabled={busy} onChange={(event) => change(updateStep(values, index, event.target.value))} placeholder={index === 0 ? "e.g. Main road pe Reliance Smart ke baad left" : "Next step"} className={`${field} mt-0 min-h-10 flex-1`} />
            <button type="button" disabled={busy} onClick={() => change(removeStep(values, index))} aria-label={`Remove step ${index + 1}`} className={`grid size-9 shrink-0 place-items-center rounded-full text-muted transition hover:bg-surface-strong disabled:opacity-40 ${ring}`}><X size={14} /></button>
          </li>)}
        </ol>
        {values.steps.length < MAX_STEPS && <button type="button" disabled={busy} onClick={() => change(addStep(values))} className={`mt-2 flex items-center gap-1 rounded-full px-3 py-2 text-xs font-semibold text-accent-strong transition hover:bg-accent-soft disabled:opacity-40 ${ring}`}><Plus size={13} />Add step</button>}
      </fieldset>
      <div>
        <label htmlFor={`${baseId}-gate`} className="block text-xs font-semibold text-ink-secondary">Gate note <span className="font-normal text-muted">(optional)</span></label>
        <input id={`${baseId}-gate`} value={values.gate_note} maxLength={GATE_NOTE_MAX} disabled={busy} onChange={(event) => change({ ...values, gate_note: event.target.value })} placeholder="e.g. Blue gate, bell on the left" className={`${field} min-h-11`} />
      </div>
      <fieldset>
        <legend className="text-xs font-semibold text-ink-secondary">Handover <span className="font-normal text-muted">(optional)</span></legend>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {HANDOVER_CHIPS.map((chip) => <button key={chip} type="button" aria-pressed={handover === chip} disabled={busy} onClick={() => change(pickHandover(values, chip))} className={`rounded-full px-3 py-1.5 text-[11px] font-semibold transition disabled:opacity-60 ${ring} ${handover === chip ? "bg-accent text-on-accent" : "border border-accent-border text-ink-secondary"}`}>{chip}</button>)}
        </div>
        <label htmlFor={`${baseId}-handover`} className="sr-only">Handover instructions</label>
        <input id={`${baseId}-handover`} value={values.handover_instructions} maxLength={HANDOVER_MAX} disabled={busy} onChange={(event) => change({ ...values, handover_instructions: event.target.value })} placeholder="Or write your own" className={`${field} min-h-11`} />
      </fieldset>
      {error && <p role="alert" className="rounded-xl bg-danger-soft px-3 py-2 text-xs leading-5 text-danger-hover">{error}</p>}
    </form>
  </InputDialog>;
}
