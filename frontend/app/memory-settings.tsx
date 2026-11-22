"use client";

import { ArrowCounterClockwise, Check, CircleNotch, PencilSimple, Sparkle, Trash, Warning } from "@phosphor-icons/react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ApiError } from "./authenticated-api";
import { PREFERENCE_CATEGORIES, RouteUnavailableError, useMemoryApi, type MemoryPermission } from "./memory-api";
import { correctionPayload, editableFields, groupMemories, preferencePermissionState, type MemoryRecord, type MemoryRow } from "./memory-groups.mjs";
import { initialEditValues } from "./input-flows.mjs";
import { ConfirmDialog } from "./ui/confirm-dialog";
import { InputDialog } from "./ui/input-dialog";
import { hasUnsavedChanges } from "./ui/input-dialog-logic.mjs";

const ring = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus";
const BADGE_STYLES: Record<string, string> = {
  "Just learned": "bg-success-soft text-success",
  "You told us": "bg-accent-soft text-accent-strong",
  Suggested: "bg-zepto-soft text-zepto-strong",
};

function errorText(cause: unknown, fallback: string) {
  if (cause instanceof ApiError && cause.message && cause.message !== "Request failed." && cause.status < 500) return cause.message;
  return fallback;
}

/** Settings → "Saved preferences": view, edit, undo and forget explicit memories. */
export function MemorySettings() {
  const api = useMemoryApi();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const forgetAllButtonRef = useRef<HTMLButtonElement>(null);
  const [memories, setMemories] = useState<MemoryRecord[] | undefined>(undefined);
  const [loadError, setLoadError] = useState("");
  const [permissions, setPermissions] = useState<MemoryPermission[] | undefined>(undefined);
  const [permissionError, setPermissionError] = useState("");
  const [permissionBusy, setPermissionBusy] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [undoAvailable, setUndoAvailable] = useState(true);
  const [forgetAllAvailable, setForgetAllAvailable] = useState(true);
  const [forgetAll, setForgetAll] = useState<{ token: string; count?: number } | null>(null);
  const [forgetAllBusy, setForgetAllBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const loadMemories = useCallback(async () => {
    setLoadError("");
    try {
      const loaded = await api.list();
      setMemories(Array.isArray(loaded) ? loaded : []);
      setNow(Date.now());
    } catch (cause) {
      setMemories((current) => current ?? []);
      setLoadError(errorText(cause, "Could not load saved preferences. Please try again in a little while."));
    }
  }, [api]);

  const loadPermissions = useCallback(async () => {
    setPermissionError("");
    try {
      setPermissions(await api.permissions());
    } catch (cause) {
      setPermissions([]);
      setPermissionError(errorText(cause, "Could not load this setting."));
    }
  }, [api]);

  useEffect(() => {
    void loadMemories();
    void loadPermissions();
  }, [loadMemories, loadPermissions]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  const view = groupMemories(memories ?? [], now);
  const permissionState = permissions ? preferencePermissionState(permissions, PREFERENCE_CATEGORIES) : undefined;

  async function act(id: string, run: () => Promise<unknown>, success: string, failure: string) {
    setBusyId(id);
    setNotice(null);
    try {
      await run();
      setNotice({ tone: "ok", text: success });
      await loadMemories();
      return true;
    } catch (cause) {
      if (cause instanceof RouteUnavailableError) return false;
      setNotice({ tone: "error", text: errorText(cause, failure) });
      return false;
    } finally {
      setBusyId(null);
    }
  }

  async function togglePermission() {
    const allowed = permissionState !== "on";
    setPermissionBusy(true);
    setPermissionError("");
    try {
      await Promise.all(PREFERENCE_CATEGORIES.map((category) => api.setPreferencePermission(category, allowed)));
      setNotice({ tone: "ok", text: allowed ? "Your preferences will now be used while shopping." : "Turned off. Saved preferences are kept but not used." });
    } catch (cause) {
      setPermissionError(errorText(cause, "Could not save this setting."));
    } finally {
      await loadPermissions();
      setPermissionBusy(false);
    }
  }

  async function remove(row: MemoryRow) {
    const removed = await act(row.id, () => api.remove(row.id), `Forgotten: “${row.sentence}”`, "Could not delete.");
    if (removed) headingRef.current?.focus();
  }

  async function undo(row: MemoryRow) {
    setBusyId(row.id);
    setNotice(null);
    try {
      await api.undo(row.id);
      setNotice({ tone: "ok", text: `Undone: “${row.sentence}”` });
      await loadMemories();
      headingRef.current?.focus();
    } catch (cause) {
      if (cause instanceof RouteUnavailableError) {
        setUndoAvailable(false);
        setNotice({ tone: "error", text: "Undo isn't available right now. You can delete it instead." });
      } else setNotice({ tone: "error", text: errorText(cause, "Could not undo.") });
    } finally {
      setBusyId(null);
    }
  }

  async function startForgetAll() {
    setForgetAllBusy(true);
    setNotice(null);
    try {
      const issued = await api.forgetAllToken();
      if (!issued?.confirm_token) throw new RouteUnavailableError();
      setForgetAll({ token: issued.confirm_token, count: issued.count });
    } catch (cause) {
      if (cause instanceof RouteUnavailableError) {
        setForgetAllAvailable(false);
        setNotice({ tone: "error", text: "Forgetting everything at once isn't available right now. You can delete each item separately." });
        headingRef.current?.focus();
      } else setNotice({ tone: "error", text: errorText(cause, "That didn't work. Please try again.") });
    } finally {
      setForgetAllBusy(false);
    }
  }

  async function confirmForgetAll() {
    if (!forgetAll) return;
    setForgetAllBusy(true);
    try {
      await api.forgetAll(forgetAll.token);
      setForgetAll(null);
      setNotice({ tone: "ok", text: "Everything forgotten. Nothing is saved now." });
      await loadMemories();
      headingRef.current?.focus();
    } catch (cause) {
      setForgetAll(null);
      if (cause instanceof RouteUnavailableError) setForgetAllAvailable(false);
      setNotice({ tone: "error", text: errorText(cause, "Could not forget everything. Some items may not have been deleted—please check the list.") });
      await loadMemories();
    } finally {
      setForgetAllBusy(false);
    }
  }

  function cancelForgetAll() {
    setForgetAll(null);
    window.requestAnimationFrame(() => forgetAllButtonRef.current?.focus());
  }

  const editingRow = editingId ? view.groups.flatMap((group) => group.rows).find((row) => row.id === editingId) ?? null : null;

  return <div className="space-y-4">
    <div>
      <h3 ref={headingRef} tabIndex={-1} className="sr-only focus:outline-none">Saved preferences</h3>
      <p className="text-xs leading-5 text-muted">Only what you told GharSaathi or approved. Change or remove anything at any time.</p>
    </div>

    <PermissionCard state={permissionState} busy={permissionBusy} error={permissionError} onToggle={() => void togglePermission()} />

    <p aria-live="polite" role="status" className={`text-xs leading-5 ${notice ? "" : "sr-only"} ${notice?.tone === "error" ? "text-danger" : "text-success"}`}>{notice?.text ?? ""}</p>

    {memories === undefined
      ? <div className="space-y-2" aria-label="Loading memories" role="status"><div className="h-16 animate-pulse rounded-2xl bg-surface-strong" /><div className="h-16 animate-pulse rounded-2xl bg-surface-strong" /></div>
      : <>
        {loadError && <div role="alert" className="flex items-start gap-2 rounded-2xl border border-danger-border bg-danger-soft p-3 text-xs leading-5 text-danger-hover"><Warning size={16} className="mt-0.5 shrink-0" /><span className="min-w-0 flex-1">{loadError}</span><button type="button" onClick={() => void loadMemories()} className={`min-h-9 shrink-0 rounded-lg border border-accent-border px-2.5 font-semibold ${ring}`}>Retry</button></div>}

        {view.suggestions.length > 0 && <section aria-labelledby="memory-suggestions" className="rounded-2xl border border-zepto-border bg-zepto-soft p-3">
          <h4 id="memory-suggestions" className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[.13em] text-zepto-muted"><Sparkle size={14} weight="fill" />Suggestions</h4>
          <p className="mt-1 text-[11px] leading-4 text-zepto-muted">Saved only if you choose Remember.</p>
          <ul className="mt-2 space-y-2">
            {view.suggestions.map((row) => <li key={row.id} className="rounded-xl bg-white p-3">
              <p className="break-words text-sm font-semibold">{row.sentence}</p>
              {row.date && <p className="mt-0.5 text-[11px] text-muted">{row.date}</p>}
              <div className="mt-2 flex flex-wrap gap-2">
                <button type="button" disabled={busyId !== null} onClick={() => void act(row.id, () => api.confirm(row.id), `Remembered: “${row.sentence}”`, "Could not save.")} aria-label={`Remember: ${row.sentence}`} className={`flex min-h-10 items-center gap-1 rounded-xl bg-accent px-3 text-xs font-semibold text-on-accent disabled:opacity-50 ${ring}`}>{busyId === row.id ? <CircleNotch size={14} className="animate-spin" /> : <Check size={14} weight="bold" />}Remember</button>
                <button type="button" disabled={busyId !== null} onClick={() => void act(row.id, () => api.reject(row.id), "Okay, this won't be saved.", "That didn't work.")} aria-label={`Dismiss: ${row.sentence}`} className={`min-h-10 rounded-xl border border-accent-border px-3 text-xs font-semibold text-ink-secondary disabled:opacity-50 ${ring}`}>Dismiss</button>
              </div>
            </li>)}
          </ul>
        </section>}

        {view.total === 0 && !loadError && <div className="rounded-2xl border border-dashed border-accent-border bg-surface px-4 py-5 text-sm leading-6 text-muted">Nothing saved yet. Just tell GharSaathi, for example ‘always get Amul milk’.</div>}

        {view.groups.map((group) => <section key={group.id} aria-labelledby={`memory-group-${group.id}`}>
          <h4 id={`memory-group-${group.id}`} className="px-1 text-[11px] font-bold uppercase tracking-[.13em] text-muted">{group.title} <span className="font-semibold text-subtle">· {group.rows.length}</span></h4>
          <ul className="mt-2 overflow-hidden rounded-2xl border border-border bg-surface">
            {group.rows.map((row, index) => <MemoryItemRow
              key={row.id}
              row={row}
              first={index === 0}
              busy={busyId === row.id}
              disabled={busyId !== null}
              showUndo={undoAvailable && row.undoable}
              onEdit={() => { setEditingId(row.id); setNotice(null); }}
              onDelete={() => void remove(row)}
              onUndo={() => void undo(row)}
            />)}
          </ul>
        </section>)}

        {forgetAllAvailable && view.total + view.suggestions.length > 0 && <button ref={forgetAllButtonRef} type="button" onClick={() => void startForgetAll()} disabled={forgetAllBusy || busyId !== null} className={`flex min-h-11 w-full items-center justify-center gap-2 rounded-xl border border-accent-border bg-surface px-4 text-sm font-semibold text-danger hover:bg-danger-soft disabled:opacity-55 ${ring}`}>{forgetAllBusy && !forgetAll ? <CircleNotch size={16} className="animate-spin" /> : <Trash size={16} weight="bold" />}Forget everything</button>}
      </>}

    <EditPreferenceDialog
      row={editingRow}
      busy={editingRow !== null && busyId === editingRow.id}
      error={editingRow && notice?.tone === "error" ? notice.text : ""}
      onClose={() => setEditingId(null)}
      onSave={async (row, edits) => {
        const payload = correctionPayload(row.memory, edits);
        if (!payload) { setEditingId(null); return; }
        const saved = await act(row.id, () => api.correct(row.id, payload), "Updated.", "Could not save your change.");
        if (saved) setEditingId(null);
      }}
    />

    <ForgetAllDialog
      open={forgetAll !== null}
      count={forgetAll?.count ?? view.total + view.suggestions.length}
      busy={forgetAllBusy}
      onCancel={cancelForgetAll}
      onConfirm={() => void confirmForgetAll()}
    />
  </div>;
}

function PermissionCard({ state, busy, error, onToggle }: { state: "on" | "off" | "partial" | undefined; busy: boolean; error: string; onToggle: () => void }) {
  const labelId = useId();
  const descriptionId = useId();
  const on = state === "on";
  const description = state === undefined
    ? "Loading…"
    : on
      ? "Your preferences are used in search and baskets."
      : state === "partial"
        ? "Only some preferences are in use. Turn this on to use all of them."
        : "Off. Saved preferences stay visible but are not used while shopping.";
  return <div className="rounded-2xl border border-border bg-surface p-3.5">
    <div className="flex items-center gap-3">
      <div className="min-w-0 flex-1"><p id={labelId} className="text-sm font-semibold">Use saved preferences</p><p id={descriptionId} className="mt-0.5 text-xs leading-5 text-muted">{description}</p></div>
      <button type="button" role="switch" aria-checked={on} aria-labelledby={labelId} aria-describedby={descriptionId} disabled={state === undefined || busy} onClick={onToggle} className={`relative h-7 w-12 shrink-0 rounded-full transition disabled:opacity-55 ${on ? "bg-accent" : "bg-border-strong"} ${ring} focus-visible:ring-offset-2`}>
        <span className={`absolute top-0.5 grid size-6 place-items-center rounded-full bg-white shadow transition-[left] ${on ? "left-[22px]" : "left-0.5"}`}>{busy && <CircleNotch size={12} className="animate-spin text-accent" />}</span>
      </button>
    </div>
    {error && <p role="alert" className="mt-2 text-[11px] text-danger">{error}</p>}
  </div>;
}

function MemoryItemRow({ row, first, busy, disabled, showUndo, onEdit, onDelete, onUndo }: {
  row: MemoryRow;
  first: boolean;
  busy: boolean;
  disabled: boolean;
  showUndo: boolean;
  onEdit: () => void;
  onDelete: () => void;
  onUndo: () => void;
}) {
  const small = `flex min-h-9 items-center gap-1 rounded-lg px-2.5 text-xs font-semibold disabled:opacity-50 ${ring}`;
  return <li className={`px-3.5 py-3 ${first ? "" : "border-t border-border"}`}>
    <p className="break-words text-sm font-semibold leading-5">{row.sentence}</p>
    <p className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[11px] text-muted">
      <span className={`rounded-full px-2 py-0.5 font-semibold ${BADGE_STYLES[row.badge] ?? BADGE_STYLES["You told us"]}`}>{row.badge}</span>
      {row.date && <span>{row.date}</span>}
    </p>
    <div className="mt-2 flex flex-wrap gap-1.5">
      {showUndo && <button type="button" disabled={disabled} onClick={onUndo} aria-label={`Undo: ${row.sentence}`} className={`${small} bg-success-soft text-success`}>{busy ? <CircleNotch size={13} className="animate-spin" /> : <ArrowCounterClockwise size={13} weight="bold" />}Undo</button>}
      <button type="button" disabled={disabled} onClick={onEdit} aria-label={`Edit: ${row.sentence}`} className={`${small} border border-accent-border text-ink-secondary`}><PencilSimple size={13} weight="bold" />Edit</button>
      <button type="button" disabled={disabled} onClick={onDelete} aria-label={`Delete: ${row.sentence}`} className={`${small} border border-danger-border text-danger`}>{busy && !showUndo ? <CircleNotch size={13} className="animate-spin" /> : <Trash size={13} weight="bold" />}Delete</button>
    </div>
  </li>;
}

/** "Edit preference" popup. Keeps the last row while the popup animates out. */
function EditPreferenceDialog({ row, busy, error, onClose, onSave }: { row: MemoryRow | null; busy: boolean; error: string; onClose: () => void; onSave: (row: MemoryRow, edits: Record<string, string>) => Promise<void> }) {
  const baseId = useId();
  const formId = `${baseId}-form`;
  const [draft, setDraft] = useState<{ row: MemoryRow; open: boolean; initial: Record<string, string>; values: Record<string, string> } | null>(null);
  if (row && (!draft?.open || draft.row.id !== row.id)) {
    const initial = initialEditValues(editableFields(row.memory));
    setDraft({ row, open: true, initial, values: initial });
  } else if (!row && draft?.open) setDraft({ ...draft, open: false });
  const shown = draft?.row ?? row;
  const fields = shown ? editableFields(shown.memory) : [];
  const dirty = row !== null && draft !== null && hasUnsavedChanges(draft.initial, draft.values);

  return <InputDialog
    open={row !== null}
    title="Edit preference"
    description={shown ? <span className="break-words">{shown.sentence}</span> : undefined}
    dirty={dirty}
    busy={busy}
    discardMessage="Discard your changes to this preference?"
    closeLabel="Close edit preference"
    widthClassName="sm:max-w-md"
    onClose={onClose}
    footer={({ requestClose }) => <div className="flex items-center gap-3">
      <button type="button" disabled={busy} onClick={() => requestClose("cancel")} className={`rounded-full px-4 py-3 text-sm font-semibold text-ink-secondary transition hover:bg-surface-strong disabled:opacity-40 ${ring}`}>Cancel</button>
      <button type="submit" form={formId} disabled={busy} className={`ml-auto flex flex-1 items-center justify-center gap-1.5 rounded-full bg-inverse px-5 py-3 text-sm font-semibold text-on-inverse disabled:opacity-50 sm:flex-none ${ring}`}>{busy && <CircleNotch size={15} className="animate-spin" />}{busy ? "Saving…" : "Save"}</button>
    </div>}
  >
    {shown && draft && <form id={formId} onSubmit={(event) => { event.preventDefault(); if (!busy) void onSave(shown, draft.values); }} aria-label={`Edit: ${shown.sentence}`} className="space-y-3">
      {fields.map((field) => <div key={field.key}>
        <label htmlFor={`${baseId}-${field.key}`} className="block text-xs font-semibold text-ink-secondary">{field.label}</label>
        <input id={`${baseId}-${field.key}`} value={draft.values[field.key] ?? ""} maxLength={field.key === "text" ? 300 : 160} disabled={busy} onChange={(event) => { const value = event.target.value; setDraft((current) => current && { ...current, values: { ...current.values, [field.key]: value } }); }} className="mt-1 min-h-11 w-full rounded-xl border border-border-strong bg-white px-3 text-sm outline-none focus:border-accent focus-visible:ring-2 focus-visible:ring-focus/30 disabled:opacity-60" />
      </div>)}
      {error && <p role="alert" className="rounded-xl bg-danger-soft px-3 py-2 text-xs leading-5 text-danger-hover">{error}</p>}
    </form>}
  </InputDialog>;
}

function ForgetAllDialog({ open, count, busy, onCancel, onConfirm }: { open: boolean; count: number; busy: boolean; onCancel: () => void; onConfirm: () => void }) {
  return <ConfirmDialog
    open={open}
    tone="destructive"
    title="Forget everything?"
    message={<>{count} saved {count === 1 ? "item" : "items"} will be permanently deleted—preferences, nevers, rules and suggestions. This can&apos;t be undone.</>}
    confirmLabel="Forget everything"
    busyLabel="Forgetting…"
    busy={busy}
    onClose={onCancel}
    onConfirm={onConfirm}
  />;
}
