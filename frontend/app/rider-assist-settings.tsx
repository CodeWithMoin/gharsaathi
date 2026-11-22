"use client";

import { CircleNotch, PencilSimple, Phone, Trash } from "@phosphor-icons/react";
import { useCallback, useEffect, useId, useState } from "react";
import { useApiRequest } from "./authenticated-api";
import {
  HANDOFF_OPTIONS,
  NOTES_LANGUAGES,
  RIDER_CALLS_PATH,
  RIDER_SETTINGS_PATH,
  RIDER_SETUP_DOC,
  notesDraft,
  notesError,
  notesPayload,
  riderCallCardText,
  riderStatus,
  type NotesDraft,
  type RiderCall,
  type RiderSettings,
} from "./rider-assist.mjs";
import { BetaTag } from "./ui/beta-tag";
import { ConfirmDialog } from "./ui/confirm-dialog";
import { InputDialog } from "./ui/input-dialog";

const ring = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus";

const SETUP_STEPS = [
  "Get a Vobiz account (India KYC) and a Bengaluru 080 number.",
  "Your server admin turns on RIDER_ASSIST_ENABLED, sets your account as the owner, and points the number's answer URL at GharSaathi.",
  "On your phone, forward unanswered, busy and unreachable calls to that number (dial **61*, **67*, **62*; ##002# turns it off).",
  "Save delivery notes below, then turn Rider assist on.",
];

/** Settings → Rider assist (Beta): switch, delivery notes, setup checklist, recent calls. */
export function RiderAssistSettings() {
  const request = useApiRequest();
  const labelId = useId();
  const descriptionId = useId();
  const [settings, setSettings] = useState<RiderSettings | null>(null);
  const [calls, setCalls] = useState<RiderCall[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);

  const loadCalls = useCallback(async () => {
    const { calls: loaded } = await request<{ calls: RiderCall[] }>(RIDER_CALLS_PATH);
    setCalls(loaded);
  }, [request]);

  useEffect(() => {
    let active = true;
    void Promise.all([request<RiderSettings>(RIDER_SETTINGS_PATH), request<{ calls: RiderCall[] }>(RIDER_CALLS_PATH)])
      .then(([loadedSettings, loadedCalls]) => { if (active) { setSettings(loadedSettings); setCalls(loadedCalls.calls); } })
      .catch(() => { if (active) setError("Could not load rider assist settings."); });
    return () => { active = false; };
  }, [request]);

  const save = useCallback(async (body: Record<string, unknown>) => {
    const saved = await request<RiderSettings>(RIDER_SETTINGS_PATH, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    setSettings(saved);
    return saved;
  }, [request]);

  async function toggle() {
    if (!settings || busy) return;
    setBusy(true);
    setError("");
    try {
      await save({ enabled: !settings.enabled });
    } catch {
      setError("Could not change rider assist. Try again.");
    } finally {
      setBusy(false);
    }
  }

  async function removeCall(id: string) {
    setError("");
    try {
      await request(`${RIDER_CALLS_PATH}/${encodeURIComponent(id)}`, { method: "DELETE" });
      setCalls((current) => current.filter((call) => call.id !== id));
    } catch {
      setError("Could not delete that call. Try again.");
    }
  }

  async function clearCalls() {
    setBusy(true);
    try {
      await request(RIDER_CALLS_PATH, { method: "DELETE" });
      setCalls([]);
      setConfirmClear(false);
    } catch {
      setError("Could not delete the calls. Try again.");
    } finally {
      setBusy(false);
    }
  }

  const status = riderStatus(settings);
  const on = Boolean(settings?.enabled);
  const notes = settings?.notes;
  const handoff = HANDOFF_OPTIONS.find((option) => option.value === notes?.handoff)?.label ?? "Call me again";

  return <div className="space-y-4">
    <div className="rounded-2xl border border-border bg-surface p-4">
      <div className="flex items-center gap-2"><span className="grid size-10 place-items-center rounded-xl bg-accent-soft text-accent-hover"><Phone size={21} weight="duotone" /></span><h3 className="text-base font-semibold tracking-[-.02em]">Rider assist</h3><BetaTag /></div>
      <p className="mt-2 text-sm leading-6 text-muted">When you miss a delivery partner&apos;s call, an AI assistant answers it, says it&apos;s an AI, and helps with directions from your notes. It never shares OTPs, agrees to charges, or changes your order. You get a message here, never a call.</p>
      <div className="mt-4 flex items-center gap-3 rounded-xl border border-border px-3 py-2.5">
        <div className="min-w-0 flex-1"><p id={labelId} className="text-sm font-semibold">Answer missed delivery calls</p><p id={descriptionId} className="mt-0.5 text-xs leading-5 text-muted">{settings && settings.enabled && !status.active ? `On, but ${status.label.toLowerCase()}` : status.label}</p></div>
        <button type="button" role="switch" aria-checked={on} aria-labelledby={labelId} aria-describedby={descriptionId} disabled={!settings || busy} onClick={() => void toggle()} className={`relative h-7 w-12 shrink-0 rounded-full transition disabled:opacity-55 ${on ? "bg-accent" : "bg-border-strong"} ${ring} focus-visible:ring-offset-2`}>
          <span className={`absolute top-0.5 grid size-6 place-items-center rounded-full bg-white shadow transition-[left] ${on ? "left-[22px]" : "left-0.5"}`}>{busy && <CircleNotch size={12} className="animate-spin text-accent-ink" />}</span>
        </button>
      </div>
      {error && <p role="alert" className="mt-2 text-[11px] text-danger">{error}</p>}
    </div>

    <div className="rounded-2xl border border-border bg-surface p-4">
      <h3 className="text-sm font-semibold">Delivery notes</h3>
      <p className="mt-1 text-xs leading-5 text-muted">The only directions the assistant may give a rider.</p>
      {settings
        ? <dl className="mt-3 divide-y divide-border rounded-xl border border-border text-sm">
          <div className="flex gap-3 px-3 py-2.5"><dt className="w-24 shrink-0 text-xs text-muted">Landmark</dt><dd className="min-w-0 flex-1 break-words font-semibold">{notes?.landmark || "Not set"}</dd></div>
          <div className="flex gap-3 px-3 py-2.5"><dt className="w-24 shrink-0 text-xs text-muted">Directions</dt><dd className="min-w-0 flex-1 break-words font-semibold">{notes?.directions || "Not set"}</dd></div>
          <div className="flex gap-3 px-3 py-2.5"><dt className="w-24 shrink-0 text-xs text-muted">If you&apos;re away</dt><dd className="min-w-0 flex-1 font-semibold">{handoff}</dd></div>
          <div className="flex gap-3 px-3 py-2.5"><dt className="w-24 shrink-0 text-xs text-muted">Language</dt><dd className="min-w-0 flex-1 font-semibold">{NOTES_LANGUAGES.find((option) => option.value === notes?.preferred_language)?.label ?? "Hinglish"}</dd></div>
        </dl>
        : <div className="mt-3 h-36 animate-pulse rounded-xl bg-surface-strong" aria-label="Loading delivery notes" />}
      <button type="button" onClick={() => setEditing(true)} disabled={!settings} className={`mt-3 flex min-h-11 w-full items-center justify-center gap-2 rounded-xl bg-inverse px-4 text-sm font-semibold text-on-inverse disabled:opacity-50 ${ring}`}><PencilSimple size={16} weight="bold" />Edit delivery notes</button>
    </div>

    <div className="rounded-2xl border border-border bg-surface p-4">
      <h3 className="text-sm font-semibold">Setup checklist</h3>
      <ol className="mt-2 list-decimal space-y-1.5 pl-5 text-xs leading-5 text-muted">{SETUP_STEPS.map((step) => <li key={step}>{step}</li>)}</ol>
      <p className="mt-2 text-[11px] leading-4 text-muted">Full steps, forwarding codes and costs: <code className="rounded bg-surface-strong px-1">{RIDER_SETUP_DOC}</code> in the GharSaathi repository.</p>
    </div>

    <div className="rounded-2xl border border-border bg-surface p-4">
      <div className="flex items-center justify-between gap-2"><h3 className="text-sm font-semibold">Recent rider calls</h3>{calls.length > 0 && <button type="button" onClick={() => setConfirmClear(true)} className={`rounded-full px-2 py-1 text-xs font-semibold text-danger ${ring}`}>Delete all</button>}</div>
      <p className="mt-1 text-xs leading-5 text-muted">A short summary of each call. No audio is recorded or kept.</p>
      {calls.length
        ? <ul className="mt-3 divide-y divide-border rounded-xl border border-border">{calls.map((call) => <li key={call.id} className="flex items-start gap-2 px-3 py-2.5">
          <div className="min-w-0 flex-1"><p className="text-xs font-semibold leading-5">{riderCallCardText(call)}</p><p className="mt-0.5 text-[11px] leading-4 text-muted">{call.summary}</p></div>
          <button type="button" onClick={() => void removeCall(call.id)} aria-label="Delete this rider call" className={`grid size-9 shrink-0 place-items-center rounded-full text-muted hover:bg-surface-strong ${ring}`}><Trash size={16} /></button>
        </li>)}</ul>
        : <p className="mt-3 text-xs text-muted">No rider calls yet.</p>}
      <button type="button" onClick={() => void loadCalls().catch(() => setError("Could not load rider calls."))} className={`mt-3 min-h-10 w-full rounded-xl border border-accent-border px-4 text-xs font-semibold text-ink-secondary ${ring}`}>Refresh</button>
    </div>

    <DeliveryNotesDialog
      open={editing && settings !== null}
      initial={notesDraft(settings?.notes)}
      onClose={() => setEditing(false)}
      onSave={async (draft) => { await save({ notes: notesPayload(draft) }); setEditing(false); }}
    />
    <ConfirmDialog
      open={confirmClear}
      tone="destructive"
      title="Delete all rider calls?"
      message="Every saved rider-call summary will be deleted. This can't be undone."
      confirmLabel="Delete all"
      busyLabel="Deleting…"
      busy={busy}
      onClose={() => setConfirmClear(false)}
      onConfirm={() => void clearCalls()}
    />
  </div>;
}

function DeliveryNotesDialog({ open, initial, onClose, onSave }: { open: boolean; initial: NotesDraft; onClose: () => void; onSave: (draft: NotesDraft) => Promise<void> }) {
  const baseId = useId();
  const formId = `${baseId}-form`;
  const [draft, setDraft] = useState<NotesDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (open && !draft) setDraft(initial);
  else if (!open && draft && !busy) setDraft(null);
  const values = draft ?? initial;
  const dirty = open && draft !== null && JSON.stringify(draft) !== JSON.stringify(initial);
  const update = (patch: Partial<NotesDraft>) => { setError(""); setDraft((current) => ({ ...(current ?? initial), ...patch })); };

  async function submit() {
    if (busy) return;
    const problem = notesError(values);
    if (problem) { setError(problem); return; }
    setBusy(true);
    try {
      await onSave(values);
    } catch (cause) {
      setError(cause instanceof Error && cause.message !== "Request failed." ? cause.message : "Could not save. Check the notes and try again.");
    } finally {
      setBusy(false);
    }
  }

  const field = `mt-1.5 w-full rounded-xl border border-border-strong bg-white px-3 text-sm outline-none focus:border-accent focus-visible:ring-2 focus-visible:ring-focus/30`;
  const choice = (selected: boolean) => `min-h-11 rounded-xl px-2 text-xs font-semibold transition ${ring} disabled:opacity-60 ${selected ? "bg-accent text-on-accent" : "border border-accent-border text-ink-secondary"}`;
  return <InputDialog
    open={open}
    title="Delivery notes"
    description="Only what you write here is told to a delivery partner. Don't add OTPs, PINs or payment details."
    dirty={dirty}
    busy={busy}
    discardMessage="Discard your delivery note changes?"
    closeLabel="Close delivery notes"
    widthClassName="sm:max-w-md"
    onClose={() => { setError(""); onClose(); }}
    footer={({ requestClose }) => <div className="flex items-center gap-3">
      <button type="button" disabled={busy} onClick={() => requestClose("cancel")} className="rounded-full px-4 py-3 text-sm font-semibold text-ink-secondary transition hover:bg-surface-strong disabled:opacity-40">Cancel</button>
      <button type="submit" form={formId} disabled={busy} className="ml-auto flex flex-1 items-center justify-center gap-1.5 rounded-full bg-inverse px-5 py-3 text-sm font-semibold text-on-inverse disabled:opacity-50 sm:flex-none">{busy && <CircleNotch size={15} className="animate-spin" />}{busy ? "Saving…" : "Save"}</button>
    </div>}
  >
    <form id={formId} onSubmit={(event) => { event.preventDefault(); void submit(); }} className="space-y-5">
      <div>
        <label htmlFor={`${baseId}-landmark`} className="block text-xs font-semibold text-ink-secondary">Landmark</label>
        <input id={`${baseId}-landmark`} value={values.landmark} maxLength={160} disabled={busy} onChange={(event) => update({ landmark: event.target.value })} placeholder="e.g. Opposite Shiv mandir" className={`${field} min-h-11`} />
      </div>
      <div>
        <label htmlFor={`${baseId}-directions`} className="block text-xs font-semibold text-ink-secondary">Gate, tower and flat directions</label>
        <textarea id={`${baseId}-directions`} value={values.directions} maxLength={300} rows={3} disabled={busy} onChange={(event) => update({ directions: event.target.value })} placeholder="e.g. Enter by gate 2, Tower B, flat 402, lift ke saamne" className={`${field} py-2.5`} />
      </div>
      <fieldset>
        <legend className="text-xs font-semibold text-ink-secondary">If nobody opens the door</legend>
        <div className="mt-1.5 grid grid-cols-2 gap-2">{HANDOFF_OPTIONS.map((option) => <button key={option.value} type="button" aria-pressed={values.handoff === option.value} disabled={busy} onClick={() => update({ handoff: option.value })} className={choice(values.handoff === option.value)}>{option.label}</button>)}</div>
      </fieldset>
      <fieldset>
        <legend className="text-xs font-semibold text-ink-secondary">Language for riders</legend>
        <div className="mt-1.5 grid grid-cols-3 gap-2">{NOTES_LANGUAGES.map((option) => <button key={option.value} type="button" aria-pressed={values.preferred_language === option.value} disabled={busy} onClick={() => update({ preferred_language: option.value })} className={choice(values.preferred_language === option.value)}>{option.label}</button>)}</div>
      </fieldset>
      {error && <p role="alert" className="rounded-xl bg-danger-soft px-3 py-2 text-xs leading-5 text-danger-hover">{error}</p>}
    </form>
  </InputDialog>;
}
