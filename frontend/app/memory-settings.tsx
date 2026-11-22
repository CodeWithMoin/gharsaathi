"use client";

import { ArrowCounterClockwise, Check, CircleNotch, PencilSimple, Sparkle, Trash, Warning } from "@phosphor-icons/react";
import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { ApiError } from "./authenticated-api";
import { PREFERENCE_CATEGORIES, RouteUnavailableError, useMemoryApi, type MemoryPermission } from "./memory-api";
import { correctionPayload, editableFields, groupMemories, preferencePermissionState, type MemoryRecord, type MemoryRow } from "./memory-groups.mjs";

const ring = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#c6531d]";
const BADGE_STYLES: Record<string, string> = {
  "abhi seekha": "bg-[#e3f0e7] text-[#2f6a48]",
  "aapne bataya": "bg-[#f7e8de] text-[#93471f]",
  suggestion: "bg-[#eee6f6] text-[#5d4386]",
};

function errorText(cause: unknown, fallback: string) {
  if (cause instanceof ApiError && cause.message && cause.message !== "Request failed." && cause.status < 500) return cause.message;
  return fallback;
}

/** Settings → "Yaad rakhi baatein": view, edit, undo and forget explicit memories. */
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
      setLoadError(errorText(cause, "Yaadein load nahi ho paayi. Thodi der baad phir try kijiye."));
    }
  }, [api]);

  const loadPermissions = useCallback(async () => {
    setPermissionError("");
    try {
      setPermissions(await api.permissions());
    } catch (cause) {
      setPermissions([]);
      setPermissionError(errorText(cause, "Permission load nahi ho paayi."));
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
      setNotice({ tone: "ok", text: allowed ? "Ab aapki pasand shopping mein use hogi." : "Band ho gaya. Yaadein mitti nahi, bas use nahi hongi." });
    } catch (cause) {
      setPermissionError(errorText(cause, "Setting save nahi ho paayi."));
    } finally {
      await loadPermissions();
      setPermissionBusy(false);
    }
  }

  async function remove(row: MemoryRow) {
    const removed = await act(row.id, () => api.remove(row.id), `Bhool gaye: “${row.sentence}”`, "Delete nahi ho paaya.");
    if (removed) headingRef.current?.focus();
  }

  async function undo(row: MemoryRow) {
    setBusyId(row.id);
    setNotice(null);
    try {
      await api.undo(row.id);
      setNotice({ tone: "ok", text: `Undo ho gaya: “${row.sentence}”` });
      await loadMemories();
      headingRef.current?.focus();
    } catch (cause) {
      if (cause instanceof RouteUnavailableError) {
        setUndoAvailable(false);
        setNotice({ tone: "error", text: "Undo abhi available nahi. Delete se hata sakte hain." });
      } else setNotice({ tone: "error", text: errorText(cause, "Undo nahi ho paaya.") });
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
        setNotice({ tone: "error", text: "Sab ek saath bhoolna abhi available nahi. Har yaad alag se delete kar sakte hain." });
        headingRef.current?.focus();
      } else setNotice({ tone: "error", text: errorText(cause, "Abhi nahi ho paaya. Phir try kijiye.") });
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
      setNotice({ tone: "ok", text: "Sab bhool gaye. Ab koi yaad nahi rakhi hai." });
      await loadMemories();
      headingRef.current?.focus();
    } catch (cause) {
      setForgetAll(null);
      if (cause instanceof RouteUnavailableError) setForgetAllAvailable(false);
      setNotice({ tone: "error", text: errorText(cause, "Sab bhoolna nahi ho paaya. Kuch bhi delete nahi hua ho sakta hai—list dekh lijiye.") });
      await loadMemories();
    } finally {
      setForgetAllBusy(false);
    }
  }

  function cancelForgetAll() {
    setForgetAll(null);
    forgetAllButtonRef.current?.focus();
  }

  return <div className="space-y-4">
    <div>
      <h3 ref={headingRef} tabIndex={-1} className="sr-only focus:outline-none">Yaad rakhi baatein</h3>
      <p className="text-xs leading-5 text-[#806f65]">Sirf wahi jo aapne bataya ya haan kiya. Kabhi bhi badal ya hata sakte hain.</p>
    </div>

    <PermissionCard state={permissionState} busy={permissionBusy} error={permissionError} onToggle={() => void togglePermission()} />

    <p aria-live="polite" role="status" className={`text-xs leading-5 ${notice ? "" : "sr-only"} ${notice?.tone === "error" ? "text-[#a33d2c]" : "text-[#3f7a55]"}`}>{notice?.text ?? ""}</p>

    {memories === undefined
      ? <div className="space-y-2" aria-label="Yaadein load ho rahi hain" role="status"><div className="h-16 animate-pulse rounded-2xl bg-[#f3e6dc]" /><div className="h-16 animate-pulse rounded-2xl bg-[#f3e6dc]" /></div>
      : <>
        {loadError && <div role="alert" className="flex items-start gap-2 rounded-2xl border border-[#efc9bd] bg-[#fff4ef] p-3 text-xs leading-5 text-[#8f3a2a]"><Warning size={16} className="mt-0.5 shrink-0" /><span className="min-w-0 flex-1">{loadError}</span><button type="button" onClick={() => void loadMemories()} className={`min-h-9 shrink-0 rounded-lg border border-[#e4b9aa] px-2.5 font-semibold ${ring}`}>Retry</button></div>}

        {view.suggestions.length > 0 && <section aria-labelledby="memory-suggestions" className="rounded-2xl border border-[#e2d6ee] bg-[#fbf8fe] p-3">
          <h4 id="memory-suggestions" className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[.13em] text-[#6b5193]"><Sparkle size={14} weight="fill" />Suggestions</h4>
          <p className="mt-1 text-[11px] leading-4 text-[#7b6c8c]">Haan kahenge tabhi yaad rahega.</p>
          <ul className="mt-2 space-y-2">
            {view.suggestions.map((row) => <li key={row.id} className="rounded-xl bg-white p-3">
              <p className="break-words text-sm font-semibold">{row.sentence}</p>
              {row.date && <p className="mt-0.5 text-[11px] text-[#8d766a]">{row.date}</p>}
              <div className="mt-2 flex flex-wrap gap-2">
                <button type="button" disabled={busyId !== null} onClick={() => void act(row.id, () => api.confirm(row.id), `Yaad rakh liya: “${row.sentence}”`, "Save nahi ho paaya.")} aria-label={`Haan, yaad rakho: ${row.sentence}`} className={`flex min-h-10 items-center gap-1 rounded-xl bg-[#c6531d] px-3 text-xs font-semibold text-white disabled:opacity-50 ${ring}`}>{busyId === row.id ? <CircleNotch size={14} className="animate-spin" /> : <Check size={14} weight="bold" />}Haan, yaad rakho</button>
                <button type="button" disabled={busyId !== null} onClick={() => void act(row.id, () => api.reject(row.id), "Theek hai, yaad nahi rakhenge.", "Abhi nahi ho paaya.")} aria-label={`Nahi: ${row.sentence}`} className={`min-h-10 rounded-xl border border-[#e4cabc] px-3 text-xs font-semibold text-[#68493c] disabled:opacity-50 ${ring}`}>Nahi</button>
              </div>
            </li>)}
          </ul>
        </section>}

        {view.total === 0 && !loadError && <div className="rounded-2xl border border-dashed border-[#e2cdbf] bg-[#fffdf9] px-4 py-5 text-sm leading-6 text-[#806f65]">Abhi kuch yaad nahi—bolkar bataiye, jaise ‘doodh hamesha Amul lena’.</div>}

        {view.groups.map((group) => <section key={group.id} aria-labelledby={`memory-group-${group.id}`}>
          <h4 id={`memory-group-${group.id}`} className="px-1 text-[11px] font-bold uppercase tracking-[.13em] text-[#9a7562]">{group.title} <span className="font-semibold text-[#b49a8c]">· {group.rows.length}</span></h4>
          <ul className="mt-2 overflow-hidden rounded-2xl border border-[#ead9cd] bg-[#fffdf9]">
            {group.rows.map((row, index) => <MemoryItemRow
              key={row.id}
              row={row}
              first={index === 0}
              busy={busyId === row.id}
              disabled={busyId !== null}
              editing={editingId === row.id}
              showUndo={undoAvailable && row.undoable}
              onEdit={() => { setEditingId(row.id); setNotice(null); }}
              onCancelEdit={() => setEditingId(null)}
              onSave={async (edits) => {
                const payload = correctionPayload(row.memory, edits);
                if (!payload) { setEditingId(null); return; }
                const saved = await act(row.id, () => api.correct(row.id, payload), "Badal diya.", "Badlav save nahi ho paaya.");
                if (saved) setEditingId(null);
              }}
              onDelete={() => void remove(row)}
              onUndo={() => void undo(row)}
            />)}
          </ul>
        </section>)}

        {forgetAllAvailable && view.total + view.suggestions.length > 0 && <button ref={forgetAllButtonRef} type="button" onClick={() => void startForgetAll()} disabled={forgetAllBusy || busyId !== null} className={`flex min-h-11 w-full items-center justify-center gap-2 rounded-xl border border-[#e6cfc2] bg-[#fffdf9] px-4 text-sm font-semibold text-[#9f3f2c] hover:bg-[#fff5ef] disabled:opacity-55 ${ring}`}>{forgetAllBusy && !forgetAll ? <CircleNotch size={16} className="animate-spin" /> : <Trash size={16} weight="bold" />}Sab bhool jao</button>}
      </>}

    {forgetAll && <ConfirmDialog count={forgetAll.count ?? view.total + view.suggestions.length} busy={forgetAllBusy} onCancel={cancelForgetAll} onConfirm={() => void confirmForgetAll()} />}
  </div>;
}

function PermissionCard({ state, busy, error, onToggle }: { state: "on" | "off" | "partial" | undefined; busy: boolean; error: string; onToggle: () => void }) {
  const labelId = useId();
  const descriptionId = useId();
  const on = state === "on";
  const description = state === undefined
    ? "Load ho raha hai…"
    : on
      ? "Aapki pasand search aur basket mein use hoti hai."
      : state === "partial"
        ? "Kuch pasand hi use ho rahi hain. On karke sab chalu kijiye."
        : "Band hai. Yaadein dikhengi, par shopping mein use nahi hongi.";
  return <div className="rounded-2xl border border-[#ead9cd] bg-[#fffdf9] p-3.5">
    <div className="flex items-center gap-3">
      <div className="min-w-0 flex-1"><p id={labelId} className="text-sm font-semibold">Pasand yaad rakhna</p><p id={descriptionId} className="mt-0.5 text-xs leading-5 text-[#806f65]">{description}</p></div>
      <button type="button" role="switch" aria-checked={on} aria-labelledby={labelId} aria-describedby={descriptionId} disabled={state === undefined || busy} onClick={onToggle} className={`relative h-7 w-12 shrink-0 rounded-full transition disabled:opacity-55 ${on ? "bg-[#c6531d]" : "bg-[#dccbbf]"} ${ring} focus-visible:ring-offset-2`}>
        <span className={`absolute top-0.5 grid size-6 place-items-center rounded-full bg-white shadow transition-[left] ${on ? "left-[22px]" : "left-0.5"}`}>{busy && <CircleNotch size={12} className="animate-spin text-[#c6531d]" />}</span>
      </button>
    </div>
    {error && <p role="alert" className="mt-2 text-[11px] text-[#a33d2c]">{error}</p>}
  </div>;
}

function MemoryItemRow({ row, first, busy, disabled, editing, showUndo, onEdit, onCancelEdit, onSave, onDelete, onUndo }: {
  row: MemoryRow;
  first: boolean;
  busy: boolean;
  disabled: boolean;
  editing: boolean;
  showUndo: boolean;
  onEdit: () => void;
  onCancelEdit: () => void;
  onSave: (edits: Record<string, string>) => Promise<void>;
  onDelete: () => void;
  onUndo: () => void;
}) {
  const editButtonRef = useRef<HTMLButtonElement>(null);
  const wasEditing = useRef(false);
  useEffect(() => {
    if (wasEditing.current && !editing) editButtonRef.current?.focus();
    wasEditing.current = editing;
  }, [editing]);

  const small = `flex min-h-9 items-center gap-1 rounded-lg px-2.5 text-xs font-semibold disabled:opacity-50 ${ring}`;
  return <li className={`px-3.5 py-3 ${first ? "" : "border-t border-[#f0e2d8]"}`}>
    {editing
      ? <EditForm row={row} busy={busy} onCancel={onCancelEdit} onSave={onSave} />
      : <>
        <p className="break-words text-sm font-semibold leading-5">{row.sentence}</p>
        <p className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[11px] text-[#8d766a]">
          <span className={`rounded-full px-2 py-0.5 font-semibold ${BADGE_STYLES[row.badge] ?? BADGE_STYLES["aapne bataya"]}`}>{row.badge}</span>
          {row.date && <span>{row.date}</span>}
        </p>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {showUndo && <button type="button" disabled={disabled} onClick={onUndo} aria-label={`Undo: ${row.sentence}`} className={`${small} bg-[#e3f0e7] text-[#2f6a48]`}>{busy ? <CircleNotch size={13} className="animate-spin" /> : <ArrowCounterClockwise size={13} weight="bold" />}Undo</button>}
          <button ref={editButtonRef} type="button" disabled={disabled} onClick={onEdit} aria-label={`Edit: ${row.sentence}`} className={`${small} border border-[#e4cabc] text-[#68493c]`}><PencilSimple size={13} weight="bold" />Edit</button>
          <button type="button" disabled={disabled} onClick={onDelete} aria-label={`Delete: ${row.sentence}`} className={`${small} border border-[#efcfc4] text-[#9f3f2c]`}>{busy && !showUndo ? <CircleNotch size={13} className="animate-spin" /> : <Trash size={13} weight="bold" />}Delete</button>
        </div>
      </>}
  </li>;
}

function EditForm({ row, busy, onCancel, onSave }: { row: MemoryRow; busy: boolean; onCancel: () => void; onSave: (edits: Record<string, string>) => Promise<void> }) {
  const fields = editableFields(row.memory);
  const baseId = useId();
  const firstInputRef = useRef<HTMLInputElement>(null);
  const [edits, setEdits] = useState<Record<string, string>>(() => Object.fromEntries(fields.map((field) => [field.key, field.value])));
  useEffect(() => { firstInputRef.current?.focus(); }, []);

  function onKeyDown(event: ReactKeyboardEvent) {
    if (event.key !== "Escape") return;
    event.stopPropagation();
    onCancel();
  }

  return <form onKeyDown={onKeyDown} onSubmit={(event) => { event.preventDefault(); void onSave(edits); }} aria-label={`Edit: ${row.sentence}`}>
    <div className="space-y-2">
      {fields.map((field, index) => <div key={field.key}>
        <label htmlFor={`${baseId}-${field.key}`} className="block text-[11px] font-semibold text-[#5f4a3f]">{field.label}</label>
        <input ref={index === 0 ? firstInputRef : undefined} id={`${baseId}-${field.key}`} value={edits[field.key] ?? ""} maxLength={field.key === "text" ? 300 : 160} onChange={(event) => setEdits((current) => ({ ...current, [field.key]: event.target.value }))} className="mt-1 min-h-10 w-full rounded-xl border border-[#deccbf] bg-white px-3 text-sm outline-none focus:border-[#c6531d] focus-visible:ring-2 focus-visible:ring-[#c6531d]/30" />
      </div>)}
    </div>
    <div className="mt-3 flex gap-2">
      <button type="submit" disabled={busy} className={`flex min-h-10 flex-1 items-center justify-center gap-1 rounded-xl bg-[#3b2a22] px-3 text-xs font-semibold text-white disabled:opacity-50 ${ring}`}>{busy && <CircleNotch size={13} className="animate-spin" />}{busy ? "Saving…" : "Save"}</button>
      <button type="button" disabled={busy} onClick={onCancel} className={`min-h-10 flex-1 rounded-xl border border-[#e4cabc] px-3 text-xs font-semibold text-[#68493c] disabled:opacity-50 ${ring}`}>Cancel</button>
    </div>
  </form>;
}

function ConfirmDialog({ count, busy, onCancel, onConfirm }: { count: number; busy: boolean; onCancel: () => void; onConfirm: () => void }) {
  const titleId = useId();
  const descriptionId = useId();
  const cancelRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { cancelRef.current?.focus(); }, []);

  function onKeyDown(event: ReactKeyboardEvent) {
    if (event.key === "Escape") {
      event.stopPropagation();
      if (!busy) onCancel();
    } else if (event.key === "Tab") {
      event.preventDefault();
      (document.activeElement === cancelRef.current ? confirmRef.current : cancelRef.current)?.focus();
    }
  }

  return <div className="fixed inset-0 z-[60] flex items-end justify-center bg-[#3a271e]/40 p-3 sm:items-center" onKeyDown={onKeyDown}>
    <div role="alertdialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId} className="w-full max-w-sm rounded-[26px] border border-[#ead7ca] bg-[#fffaf5] p-5 text-[#35241c] shadow-[0_24px_60px_-30px_rgba(62,34,22,.6)]">
      <h3 id={titleId} className="font-display text-lg font-semibold tracking-[-.03em]">Sab bhool jaayein?</h3>
      <p id={descriptionId} className="mt-2 text-sm leading-6 text-[#806f65]">{count} {count === 1 ? "yaad" : "yaadein"} hamesha ke liye hat jaayengi—pasand, kabhi-nahi, niyam aur suggestions. Yeh wapas nahi aayengi.</p>
      <div className="mt-5 grid grid-cols-2 gap-2">
        <button ref={cancelRef} type="button" disabled={busy} onClick={onCancel} className={`min-h-11 rounded-xl border border-[#e4cabc] px-3 text-sm font-semibold text-[#68493c] disabled:opacity-50 ${ring}`}>Rehne do</button>
        <button ref={confirmRef} type="button" disabled={busy} onClick={onConfirm} className={`flex min-h-11 items-center justify-center gap-1.5 rounded-xl bg-[#a33d2c] px-3 text-sm font-semibold text-white disabled:opacity-60 ${ring}`}>{busy && <CircleNotch size={15} className="animate-spin" />}Haan, sab bhoolo</button>
      </div>
    </div>
  </div>;
}
