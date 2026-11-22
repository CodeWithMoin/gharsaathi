"use client";

import { ArrowCounterClockwise, ArrowSquareOut, Check, CircleNotch, ClockCounterClockwise, PencilSimple, Sparkle, Trash, Warning } from "@phosphor-icons/react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ApiError } from "./authenticated-api";
import { fullResyncAvailability } from "./history-import-summary.mjs";
import { providerLabel, RETENTION_CHOICES, usePurchaseHistoryImport, type HistoryProvider } from "./purchase-history-import";
import { MEMORY_SWITCH_LABEL, MEMORY_TERM } from "./settings-map.mjs";
import { PREFERENCE_CATEGORIES, RouteUnavailableError, useMemoryApi, type MemoryPermission } from "./memory-api";
import { correctionPayload, editableFields, preferencePermissionState, profileResetPayload, summaryView, type MemoryRecord, type MemorySummaryResponse, type SummaryRow } from "./memory-groups.mjs";
import { initialEditValues } from "./input-flows.mjs";
import { ConfirmDialog } from "./ui/confirm-dialog";
import { InputDialog } from "./ui/input-dialog";
import { hasUnsavedChanges } from "./ui/input-dialog-logic.mjs";

const ring = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus";
const SECTION_HEADING = "px-1 text-[11px] font-bold uppercase tracking-[.13em] text-muted";
const BADGE_STYLES: Record<string, string> = {
  "Just learned": "bg-success-soft text-success",
  "You told us": "bg-accent-soft text-accent-strong",
  "Said in passing": "bg-accent-soft text-accent-strong",
  "From your repeated choices": "bg-zepto-soft text-zepto-strong",
  "From your orders": "bg-surface-muted text-ink-secondary",
  "From demo history": "bg-warning-soft text-warning",
};

function errorText(cause: unknown, fallback: string) {
  if (cause instanceof ApiError && cause.message && cause.message !== "Request failed." && cause.status < 500) return cause.message;
  return fallback;
}

type MemorySettingsProps = {
  /** Profile rows (name, language…) are edited on Settings → Profile. */
  onEditProfile?: () => void;
  commerceMode?: "demo" | "live";
  swiggyConnected?: boolean;
  zeptoConnected?: boolean;
};

/**
 * Settings → "What GharSaathi knows": the Memory switch, everything saved, grouped
 * (Home, Food, Usual buys, Avoid, Shopping style, From your orders) with where it came
 * from and when plus Edit / Delete, then History (order-history import) and Privacy
 * (Delete everything, Privacy Policy).
 */
export function MemorySettings({ onEditProfile, commerceMode = "demo", swiggyConnected = false, zeptoConnected = false }: MemorySettingsProps = {}) {
  const api = useMemoryApi();
  const history = usePurchaseHistoryImport({ swiggyConnected, zeptoConnected });
  const headingRef = useRef<HTMLHeadingElement>(null);
  const forgetAllButtonRef = useRef<HTMLButtonElement>(null);
  const [memories, setMemories] = useState<MemoryRecord[] | undefined>(undefined);
  const [summary, setSummary] = useState<MemorySummaryResponse | null>(null);
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
  const [clearOrders, setClearOrders] = useState(false);
  const [clearOrdersBusy, setClearOrdersBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const loadMemories = useCallback(async () => {
    setLoadError("");
    try {
      const [loaded, grouped] = await Promise.all([api.list(), api.summary()]);
      setMemories(Array.isArray(loaded) ? loaded : []);
      setSummary(grouped ?? null);
      setNow(Date.now());
    } catch (cause) {
      setMemories((current) => current ?? []);
      setLoadError(errorText(cause, "Could not load what GharSaathi knows. Please try again in a little while."));
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

  const view = summaryView(summary, memories ?? [], now);
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
      setNotice({ tone: "ok", text: allowed ? `${MEMORY_TERM} on. GharSaathi will use what you've told it while shopping.` : `${MEMORY_TERM} off. Everything stays listed here but isn't used while shopping.` });
    } catch (cause) {
      setPermissionError(errorText(cause, "Could not save this setting."));
    } finally {
      await loadPermissions();
      await loadMemories();
      setPermissionBusy(false);
    }
  }

  async function remove(row: SummaryRow) {
    const reset = row.managedBy === "profile" ? profileResetPayload(row.kind) : null;
    const run = row.managedBy === "profile"
      ? () => (reset ? api.updateProfile(reset) : Promise.resolve(null))
      : () => api.remove(row.id);
    const removed = await act(row.id, run, row.managedBy === "profile" ? `Back to the default: “${row.label}”` : `Deleted: “${row.label}”`, "Could not delete.");
    if (removed) headingRef.current?.focus();
  }

  async function undo(row: SummaryRow) {
    setBusyId(row.id);
    setNotice(null);
    try {
      await api.undo(row.id);
      setNotice({ tone: "ok", text: `Undone: “${row.label}”` });
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
        setNotice({ tone: "error", text: "Deleting everything at once isn't available right now. You can delete each item separately." });
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
      setNotice({ tone: "ok", text: "Everything deleted. Nothing is saved now." });
      await loadMemories();
      headingRef.current?.focus();
    } catch (cause) {
      setForgetAll(null);
      if (cause instanceof RouteUnavailableError) setForgetAllAvailable(false);
      setNotice({ tone: "error", text: errorText(cause, "Could not delete everything. Some items may not have been deleted—please check the list.") });
      await loadMemories();
    } finally {
      setForgetAllBusy(false);
    }
  }

  function cancelForgetAll() {
    setForgetAll(null);
    window.requestAnimationFrame(() => forgetAllButtonRef.current?.focus());
  }

  async function confirmClearOrders() {
    setClearOrdersBusy(true);
    try {
      await api.clearOrderHistory();
      setClearOrders(false);
      setNotice({ tone: "ok", text: "Order history removed from GharSaathi. Your store orders are not changed." });
      history.reload();
      await loadMemories();
      headingRef.current?.focus();
    } catch (cause) {
      setClearOrders(false);
      setNotice({ tone: "error", text: errorText(cause, "Could not remove order history.") });
    } finally {
      setClearOrdersBusy(false);
    }
  }

  const editingRow = editingId ? view.groups.flatMap((group) => group.rows).find((row) => row.id === editingId && row.record) ?? null : null;

  return <div className="space-y-4">
    <div>
      <h3 ref={headingRef} tabIndex={-1} className="sr-only focus:outline-none">What GharSaathi knows</h3>
      <p className="text-xs leading-5 text-muted">Everything GharSaathi has saved about your home, and where it came from. Change or delete anything at any time.</p>
    </div>

    <PermissionCard state={permissionState} busy={permissionBusy} error={permissionError} onToggle={() => void togglePermission()} />

    <p aria-live="polite" role="status" className={`text-xs leading-5 ${notice ? "" : "sr-only"} ${notice?.tone === "error" ? "text-danger" : "text-success"}`}>{notice?.text ?? ""}</p>

    {memories === undefined
      ? <div className="space-y-2" aria-label="Loading what GharSaathi knows" role="status"><div className="h-16 animate-pulse rounded-2xl bg-surface-strong" /><div className="h-16 animate-pulse rounded-2xl bg-surface-strong" /></div>
      : <>
        {loadError && <div role="alert" className="flex items-start gap-2 rounded-2xl border border-danger-border bg-danger-soft p-3 text-xs leading-5 text-danger-hover"><Warning size={16} className="mt-0.5 shrink-0" /><span className="min-w-0 flex-1">{loadError}</span><button type="button" onClick={() => void loadMemories()} className={`min-h-11 shrink-0 rounded-lg border border-accent-border px-3 font-semibold ${ring}`}>Retry</button></div>}

        {view.suggestions.length > 0 && <section aria-labelledby="memory-suggestions" className="rounded-2xl border border-zepto-border bg-zepto-soft p-3">
          <h4 id="memory-suggestions" className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[.13em] text-zepto-muted"><Sparkle size={14} weight="fill" />Suggestions</h4>
          <p className="mt-1 text-[11px] leading-4 text-zepto-muted">Saved only if you choose Remember.</p>
          <ul className="mt-2 space-y-2">
            {view.suggestions.map((row) => <li key={row.id} className="rounded-xl bg-white p-3">
              <p className="break-words text-sm font-semibold">{row.label}</p>
              <p className="mt-0.5 text-[11px] text-muted">{row.badge}{row.date ? ` · ${row.date}` : ""}</p>
              <div className="mt-2 flex flex-wrap gap-2">
                <button type="button" disabled={busyId !== null} onClick={() => void act(row.id, () => api.confirm(row.id), `Remembered: “${row.label}”`, "Could not save.")} aria-label={`Remember: ${row.label}`} className={`flex min-h-10 items-center gap-1 rounded-xl bg-accent px-3 text-xs font-semibold text-on-accent disabled:opacity-50 ${ring}`}>{busyId === row.id ? <CircleNotch size={14} className="animate-spin" /> : <Check size={14} weight="bold" />}Remember</button>
                <button type="button" disabled={busyId !== null} onClick={() => void act(row.id, () => api.reject(row.id), "Okay, this won't be saved.", "That didn't work.")} aria-label={`Dismiss: ${row.label}`} className={`min-h-10 rounded-xl border border-accent-border px-3 text-xs font-semibold text-ink-secondary disabled:opacity-50 ${ring}`}>Dismiss</button>
              </div>
            </li>)}
          </ul>
        </section>}

        {view.total === 0 && !loadError && <div className="rounded-2xl border border-dashed border-accent-border bg-surface px-4 py-5 text-sm leading-6 text-muted">Nothing saved yet. Just tell GharSaathi, for example ‘always get Amul milk’.</div>}

        {view.groups.map((group) => <section key={group.id} aria-labelledby={`memory-group-${group.id}`}>
          <h4 id={`memory-group-${group.id}`} className={SECTION_HEADING}>{group.title} <span className="font-semibold text-subtle">· {group.rows.length}</span></h4>
          <ul className="mt-2 overflow-hidden rounded-2xl border border-border bg-surface">
            {group.rows.map((row, index) => <MemoryItemRow
              key={row.id}
              row={row}
              first={index === 0}
              busy={busyId === row.id}
              disabled={busyId !== null}
              showUndo={undoAvailable && row.undoable}
              onEdit={row.managedBy === "profile" ? onEditProfile : () => { setEditingId(row.id); setNotice(null); }}
              onDelete={() => void remove(row)}
              onUndo={() => void undo(row)}
            />)}
          </ul>
        </section>)}
      </>}

    <HistorySection
      history={history}
      commerceMode={commerceMode}
      swiggyConnected={swiggyConnected}
      zeptoConnected={zeptoConnected}
      hasOrderRows={view.groups.some((group) => group.id === "from_orders" && group.rows.length > 0)}
      removeDisabled={busyId !== null || clearOrdersBusy}
      onRemove={() => setClearOrders(true)}
      onImported={() => void loadMemories()}
    />

    <section aria-labelledby="memory-privacy" className="space-y-2">
      <h4 id="memory-privacy" className={SECTION_HEADING}>Privacy</h4>
      <div className="rounded-2xl border border-border bg-surface p-3.5">
        <p className="text-xs leading-5 text-muted">Only you can see this. Order history is read-only and stays on GharSaathi&apos;s server, and it never becomes a guess about what&apos;s in stock at home.</p>
        {memories !== undefined && forgetAllAvailable && view.deletable > 0 && <button ref={forgetAllButtonRef} type="button" onClick={() => void startForgetAll()} disabled={forgetAllBusy || busyId !== null} className={`mt-3 flex min-h-11 w-full items-center justify-center gap-2 rounded-xl border border-danger-border bg-surface px-4 text-sm font-semibold text-danger hover:bg-danger-soft disabled:opacity-55 ${ring}`}>{forgetAllBusy && !forgetAll ? <CircleNotch size={16} className="animate-spin" /> : <Trash size={16} weight="bold" />}Delete everything</button>}
        <a href="/privacy" target="_blank" rel="noreferrer" className={`mt-3 inline-flex min-h-10 items-center gap-1.5 rounded-lg text-xs font-semibold text-accent-ink underline underline-offset-2 ${ring}`}>Privacy Policy<ArrowSquareOut size={13} weight="bold" aria-hidden="true" /></a>
      </div>
    </section>

    <EditPreferenceDialog
      row={editingRow}
      busy={editingRow !== null && busyId === editingRow.id}
      error={editingRow && notice?.tone === "error" ? notice.text : ""}
      onClose={() => setEditingId(null)}
      onSave={async (row, edits) => {
        if (!row.record) { setEditingId(null); return; }
        const payload = correctionPayload(row.record, edits);
        if (!payload) { setEditingId(null); return; }
        const saved = await act(row.id, () => api.correct(row.id, payload), "Updated.", "Could not save your change.");
        if (saved) setEditingId(null);
      }}
    />

    <ForgetAllDialog
      open={forgetAll !== null}
      count={forgetAll?.count ?? view.deletable}
      busy={forgetAllBusy}
      onCancel={cancelForgetAll}
      onConfirm={() => void confirmForgetAll()}
    />
    <ConfirmDialog
      open={clearOrders}
      tone="destructive"
      title="Remove order history?"
      message="GharSaathi will forget the past orders it imported. Your orders on Swiggy Instamart and Zepto are not changed."
      confirmLabel="Remove"
      busyLabel="Removing…"
      busy={clearOrdersBusy}
      onClose={() => setClearOrders(false)}
      onConfirm={() => void confirmClearOrders()}
    />
  </div>;
}

function PermissionCard({ state, busy, error, onToggle }: { state: "on" | "off" | "partial" | undefined; busy: boolean; error: string; onToggle: () => void }) {
  const description = state === undefined
    ? "Loading…"
    : state === "on"
      ? `${MEMORY_TERM} is on: brands, sizes and things to avoid are used in search and baskets.`
      : state === "partial"
        ? `${MEMORY_TERM} is only partly on. Turn it on to use everything below.`
        : `${MEMORY_TERM} is off. Everything stays listed here but isn't used while shopping.`;
  return <div className="rounded-2xl border border-border bg-surface p-3.5">
    <SwitchRow label={MEMORY_SWITCH_LABEL} description={description} checked={state === "on"} disabled={state === undefined} busy={busy} onToggle={onToggle} />
    {error && <p role="alert" className="mt-2 text-[11px] text-danger">{error}</p>}
  </div>;
}

/** The one on/off switch style used on this page. */
function SwitchRow({ label, description, checked, disabled = false, busy = false, onToggle }: { label: string; description: string; checked: boolean; disabled?: boolean; busy?: boolean; onToggle: () => void }) {
  const labelId = useId();
  const descriptionId = useId();
  return <div className="flex items-center gap-3">
    <div className="min-w-0 flex-1"><p id={labelId} className="text-sm font-semibold">{label}</p><p id={descriptionId} className="mt-0.5 text-xs leading-5 text-muted">{description}</p></div>
    <button type="button" role="switch" aria-checked={checked} aria-labelledby={labelId} aria-describedby={descriptionId} disabled={disabled || busy} onClick={onToggle} className={`relative h-7 w-12 shrink-0 rounded-full transition-colors after:absolute after:-inset-x-1 after:-inset-y-2 after:content-[''] disabled:opacity-55 ${checked ? "bg-accent" : "bg-border-strong"} ${ring} focus-visible:ring-offset-2`}>
      <span className={`absolute left-0.5 top-0.5 grid size-6 place-items-center rounded-full bg-white shadow transition-transform duration-200 ease-[cubic-bezier(.23,1,.32,1)] motion-reduce:transition-none ${checked ? "translate-x-5" : "translate-x-0"}`}>{busy && <CircleNotch size={12} className="animate-spin text-accent-ink" />}</span>
    </button>
  </div>;
}

type HistoryController = ReturnType<typeof usePurchaseHistoryImport>;

/**
 * History: whether GharSaathi learns usuals from past orders (read-only import), how
 * long that data is kept, Refresh / Re-import, weekly sync per store, and Remove.
 */
function HistorySection({ history, commerceMode, swiggyConnected, zeptoConnected, hasOrderRows, removeDisabled, onRemove, onImported }: {
  history: HistoryController;
  commerceMode: "demo" | "live";
  swiggyConnected: boolean;
  zeptoConnected: boolean;
  hasOrderRows: boolean;
  removeDisabled: boolean;
  onRemove: () => void;
  onImported: () => void;
}) {
  const retentionId = useId();
  const { permission, busy } = history;
  const allowed = permission?.allowed === true;
  const connectedProviders = ([swiggyConnected ? "swiggy" : null, zeptoConnected ? "zepto" : null] as const).filter((provider): provider is HistoryProvider => provider !== null);
  const fullBlocked = connectedProviders.map((provider) => ({ provider, ...fullResyncAvailability(history.statuses[provider]) })).filter((item) => !item.available);
  const fullAvailable = connectedProviders.length > 0 && fullBlocked.length < connectedProviders.length;
  const canImport = commerceMode === "live" && connectedProviders.length > 0;
  const description = permission === undefined
    ? "Loading…"
    : allowed
      ? "On. Your recent orders suggest repeat items. Nothing is added or ordered on its own."
      : commerceMode === "demo"
        ? "Off. Not available in safe demo mode."
        : !connectedProviders.length
          ? "Off. Connect a store first (Settings → Stores)."
          : "Off. Turn on to let GharSaathi read your recent orders (read-only) and learn your usuals.";
  const after = (ran: Promise<boolean>) => void ran.then((ok) => { if (ok) onImported(); });
  const small = `flex min-h-10 items-center justify-center gap-1.5 rounded-xl border border-accent-border bg-surface px-3 text-xs font-semibold text-ink-secondary disabled:opacity-45 ${ring}`;

  return <section aria-labelledby="memory-history" aria-busy={busy !== null} className="space-y-2">
    <h4 id="memory-history" className={SECTION_HEADING}>History</h4>
    <div className="rounded-2xl border border-border bg-surface p-3.5">
      <SwitchRow
        label="Learn from my orders"
        description={description}
        checked={allowed}
        disabled={permission === undefined || (!allowed && !canImport)}
        busy={busy === "enable" || busy === "skip"}
        onToggle={() => after(allowed ? history.skip() : history.enable())}
      />

      <div className="mt-3 border-t border-border pt-3">
        <label htmlFor={retentionId} className="block text-xs font-semibold text-ink-secondary">Delete imported order data after</label>
        <select id={retentionId} value={history.retentionDays} disabled={busy !== null || permission === undefined} onChange={(event) => { const days = Number(event.target.value); if (allowed) void history.changeRetention(days); else history.setRetentionDays(days); }} className="mt-1.5 min-h-11 w-full rounded-xl border border-border-strong bg-white px-3 text-sm outline-none focus:border-accent focus-visible:ring-2 focus-visible:ring-focus/30 disabled:opacity-60">
          {RETENTION_CHOICES.map((choice) => <option key={choice.days} value={choice.days}>{choice.days === 365 ? "1 year" : `${choice.days} days`}</option>)}
        </select>
      </div>

      {allowed && <div className="mt-3 grid grid-cols-2 gap-2 border-t border-border pt-3">
        <button type="button" onClick={() => after(history.refresh())} disabled={busy !== null || !canImport} className={small}>{busy === "refresh" ? <CircleNotch size={14} className="animate-spin" /> : <ClockCounterClockwise size={14} weight="bold" />}{busy === "refresh" ? "Refreshing…" : "Refresh usuals"}</button>
        <button type="button" onClick={() => after(history.fullResync())} disabled={busy !== null || !fullAvailable || !canImport} className={small}>{busy === "full" && <CircleNotch size={14} className="animate-spin" />}{busy === "full" ? "Re-importing…" : "Re-import full history"}</button>
      </div>}
      {allowed && fullBlocked.length > 0 && <p className="mt-2 text-[11px] leading-4 text-muted">{fullBlocked.map((item) => `${providerLabel(item.provider)} full re-import: ${item.label}.`).join(" ")}</p>}

      {allowed && commerceMode === "live" && connectedProviders.length > 0 && <div className="mt-3 space-y-3 border-t border-border pt-3">
        {connectedProviders.map((provider) => <SwitchRow
          key={provider}
          label={`Keep my ${provider === "swiggy" ? "Swiggy" : "Zepto"} history up to date`}
          description={`${provider === "swiggy" ? "Swiggy only shares the last 15 days, so" : "When on,"} GharSaathi reads new orders at most once a week, when you open the app. Read-only.`}
          checked={history.statuses[provider]?.auto_sync_enabled === true}
          busy={busy === "auto"}
          disabled={busy !== null}
          onToggle={() => void history.setAutoSync(provider, history.statuses[provider]?.auto_sync_enabled !== true)}
        />)}
      </div>}

      {history.result && <div role="status" className="mt-3 space-y-1 rounded-xl bg-success-soft px-3 py-2 text-[11px] leading-5 text-success">{history.result.cached ? <p>Already checked recently, so the store wasn&apos;t asked again.</p> : history.result.lines.map((line) => <p key={line}>{line}</p>)}</div>}
      {history.error && <p role="alert" className="mt-3 text-[11px] leading-4 text-danger">{history.error}</p>}

      {(allowed || hasOrderRows) && <button type="button" disabled={removeDisabled || busy !== null} onClick={onRemove} className={`mt-3 flex min-h-10 w-full items-center justify-center gap-1.5 rounded-xl border border-danger-border bg-surface px-3 text-xs font-semibold text-danger disabled:opacity-50 ${ring}`}><Trash size={13} weight="bold" />Remove order history</button>}
    </div>
  </section>;
}

function MemoryItemRow({ row, first, busy, disabled, showUndo, onEdit, onDelete, onUndo }: {
  row: SummaryRow;
  first: boolean;
  busy: boolean;
  disabled: boolean;
  showUndo: boolean;
  onEdit?: () => void;
  onDelete: () => void;
  onUndo: () => void;
}) {
  const small = `flex min-h-9 items-center gap-1 rounded-lg px-2.5 text-xs font-semibold disabled:opacity-50 ${ring}`;
  const deleteLabel = row.managedBy === "profile" ? "Reset" : "Delete";
  return <li className={`px-3.5 py-3 ${first ? "" : "border-t border-border"}`}>
    <p className="break-words text-sm font-semibold leading-5">{row.label}</p>
    <p className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[11px] text-muted">
      <span className={`rounded-full px-2 py-0.5 font-semibold ${BADGE_STYLES[row.badge] ?? BADGE_STYLES["You told us"]}`}>{row.badge}</span>
      {row.date && <span>{row.managedBy === "order" ? `Last bought ${row.date}` : `Updated ${row.date}`}</span>}
    </p>
    {(showUndo || (row.editable && onEdit) || row.deletable) && <div className="mt-2 flex flex-wrap gap-1.5">
      {showUndo && <button type="button" disabled={disabled} onClick={onUndo} aria-label={`Undo: ${row.label}`} className={`${small} bg-success-soft text-success`}>{busy ? <CircleNotch size={13} className="animate-spin" /> : <ArrowCounterClockwise size={13} weight="bold" />}Undo</button>}
      {row.editable && onEdit && <button type="button" disabled={disabled} onClick={onEdit} aria-label={`Edit: ${row.label}`} className={`${small} border border-accent-border text-ink-secondary`}><PencilSimple size={13} weight="bold" />Edit</button>}
      {row.deletable && <button type="button" disabled={disabled} onClick={onDelete} aria-label={`${deleteLabel}: ${row.label}`} className={`${small} border border-danger-border text-danger`}>{busy && !showUndo ? <CircleNotch size={13} className="animate-spin" /> : <Trash size={13} weight="bold" />}{deleteLabel}</button>}
    </div>}
  </li>;
}

/** "Edit preference" popup. Keeps the last row while the popup animates out. */
function EditPreferenceDialog({ row, busy, error, onClose, onSave }: { row: SummaryRow | null; busy: boolean; error: string; onClose: () => void; onSave: (row: SummaryRow, edits: Record<string, string>) => Promise<void> }) {
  const baseId = useId();
  const formId = `${baseId}-form`;
  const [draft, setDraft] = useState<{ row: SummaryRow; open: boolean; initial: Record<string, string>; values: Record<string, string> } | null>(null);
  if (row?.record && (!draft?.open || draft.row.id !== row.id)) {
    const initial = initialEditValues(editableFields(row.record));
    setDraft({ row, open: true, initial, values: initial });
  } else if (!row && draft?.open) setDraft({ ...draft, open: false });
  const shown = draft?.row ?? row;
  const fields = shown?.record ? editableFields(shown.record) : [];
  const dirty = row !== null && draft !== null && hasUnsavedChanges(draft.initial, draft.values);

  return <InputDialog
    open={row !== null}
    title="Edit preference"
    description={shown ? <span className="break-words">{shown.label}</span> : undefined}
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
    {shown && draft && <form id={formId} onSubmit={(event) => { event.preventDefault(); if (!busy) void onSave(shown, draft.values); }} aria-label={`Edit: ${shown.label}`} className="space-y-3">
      {fields.map((field) => <div key={field.key}>
        <label htmlFor={`${baseId}-${field.key}`} className="block text-xs font-semibold text-ink-secondary">{field.label}</label>
        {field.options
          ? <select id={`${baseId}-${field.key}`} value={draft.values[field.key] ?? ""} disabled={busy} onChange={(event) => { const value = event.target.value; setDraft((current) => current && { ...current, values: { ...current.values, [field.key]: value } }); }} className="mt-1 min-h-11 w-full rounded-xl border border-border-strong bg-white px-3 text-sm outline-none focus:border-accent focus-visible:ring-2 focus-visible:ring-focus/30 disabled:opacity-60">
            {field.options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
          : <input id={`${baseId}-${field.key}`} value={draft.values[field.key] ?? ""} maxLength={field.key === "text" ? 300 : 160} disabled={busy} onChange={(event) => { const value = event.target.value; setDraft((current) => current && { ...current, values: { ...current.values, [field.key]: value } }); }} className="mt-1 min-h-11 w-full rounded-xl border border-border-strong bg-white px-3 text-sm outline-none focus:border-accent focus-visible:ring-2 focus-visible:ring-focus/30 disabled:opacity-60" />}
      </div>)}
      {error && <p role="alert" className="rounded-xl bg-danger-soft px-3 py-2 text-xs leading-5 text-danger-hover">{error}</p>}
    </form>}
  </InputDialog>;
}

function ForgetAllDialog({ open, count, busy, onCancel, onConfirm }: { open: boolean; count: number; busy: boolean; onCancel: () => void; onConfirm: () => void }) {
  return <ConfirmDialog
    open={open}
    tone="destructive"
    title="Delete everything?"
    message={<>{count} saved {count === 1 ? "item" : "items"} will be permanently deleted—home, food, usual buys, avoid, shopping style and suggestions. Your name, language and order history stay; change them in Profile and History. This can&apos;t be undone.</>}
    confirmLabel="Delete everything"
    busyLabel="Deleting…"
    busy={busy}
    onClose={onCancel}
    onConfirm={onConfirm}
  />;
}
