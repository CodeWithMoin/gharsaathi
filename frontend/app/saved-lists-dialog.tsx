"use client";

import { CircleNotch, ListChecks, Minus, PencilSimple, Plus, ShoppingCartSimple, Trash } from "@phosphor-icons/react";
import { FormEvent, useCallback, useEffect, useId, useState } from "react";
import { ApiError, useApiRequest } from "./authenticated-api";
import { draftDirty, draftPayload, draftRemove, draftRename, draftStep, listDraft, listNameProblem, type ListDraft, type SavedList, type Store } from "./quick-actions.mjs";
import { ConfirmDialog } from "./ui/confirm-dialog";
import { InputDialog } from "./ui/input-dialog";

const ring = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus";

function errorText(cause: unknown, fallback: string) {
  if (cause instanceof ApiError && cause.message && cause.message !== "Request failed." && cause.status < 500) return cause.message;
  return fallback;
}

export type ListsDialogMode = "lists" | "save-basket";

/**
 * "My lists": the user's own saved lists. Add a list to the basket in one tap,
 * make a new one by name or from the basket, edit names, items and quantities,
 * delete. Lists are only what the user saved; nothing is suggested.
 */
export function SavedListsDialog({ open, mode, basketStores, addingListId, onClose, onAddList, onChanged }: {
  open: boolean;
  /** "save-basket" opens straight on "Save basket as list". */
  mode: ListsDialogMode;
  /** Stores whose open basket has items (for "Save basket as list"). */
  basketStores: Store[];
  addingListId: string | null;
  onClose: () => void;
  /** Adds the list to the basket; the page reports the result in the chat. */
  onAddList: (list: SavedList) => void;
  /** Lists changed (for the quick chips). */
  onChanged: () => void;
}) {
  const apiRequest = useApiRequest();
  const formId = useId();
  const nameId = useId();
  const [lists, setLists] = useState<SavedList[] | null>(null);
  const [loadError, setLoadError] = useState("");
  const [view, setView] = useState<{ kind: "lists" } | { kind: "create"; fromBasket: boolean } | { kind: "edit"; list: SavedList }>({ kind: "lists" });
  const [name, setName] = useState("");
  const [draft, setDraft] = useState<ListDraft | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState<SavedList | null>(null);

  const load = useCallback(async () => {
    setLoadError("");
    try {
      setLists((await apiRequest<{ lists: SavedList[] }>("/api/lists")).lists);
    } catch (cause) {
      setLoadError(errorText(cause, "Couldn't load your lists. Try again."));
    }
  }, [apiRequest]);

  useEffect(() => {
    if (!open) return;
    setError("");
    setName("");
    setDraft(null);
    setView(mode === "save-basket" ? { kind: "create", fromBasket: true } : { kind: "lists" });
    void load();
  }, [load, mode, open]);

  const changed = (next: SavedList[]) => { setLists(next); onChanged(); };

  async function create(event: FormEvent) {
    event.preventDefault();
    if (view.kind !== "create" || busy) return;
    const problem = listNameProblem(name, lists ?? []);
    if (problem) { setError(problem); return; }
    setBusy("create");
    setError("");
    try {
      const created = await apiRequest<SavedList>("/api/lists", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, from_baskets: view.fromBasket ? basketStores : [] }) });
      changed([created, ...(lists ?? [])]);
      setName("");
      if (created.items.length) setView({ kind: "lists" });
      else { setDraft(listDraft(created)); setView({ kind: "edit", list: created }); }
    } catch (cause) {
      setError(errorText(cause, "Couldn't save the list. Try again."));
    } finally {
      setBusy(null);
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (view.kind !== "edit" || !draft || busy) return;
    const payload = draftPayload(draft);
    if (!payload) { setError("Give the list a name under 60 characters."); return; }
    setBusy("save");
    setError("");
    try {
      const saved = await apiRequest<SavedList>(`/api/lists/${encodeURIComponent(view.list.id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      changed((lists ?? []).map((item) => item.id === saved.id ? saved : item));
      setView({ kind: "lists" });
      setDraft(null);
    } catch (cause) {
      setError(errorText(cause, "Couldn't save your changes. Try again."));
    } finally {
      setBusy(null);
    }
  }

  async function addBasketToList() {
    if (view.kind !== "edit" || busy || !basketStores.length) return;
    setBusy("add-basket");
    setError("");
    try {
      const saved = await apiRequest<SavedList>(`/api/lists/${encodeURIComponent(view.list.id)}/add-basket`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ providers: basketStores }) });
      changed((lists ?? []).map((item) => item.id === saved.id ? saved : item));
      setView({ kind: "edit", list: saved });
      setDraft(listDraft(saved));
    } catch (cause) {
      setError(errorText(cause, "Couldn't add the basket to this list."));
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    if (!deleting) return;
    setBusy("delete");
    try {
      await apiRequest<null>(`/api/lists/${encodeURIComponent(deleting.id)}`, { method: "DELETE" });
      changed((lists ?? []).filter((item) => item.id !== deleting.id));
      setDeleting(null);
      setView({ kind: "lists" });
      setDraft(null);
    } catch (cause) {
      setError(errorText(cause, "Couldn't delete the list."));
      setDeleting(null);
    } finally {
      setBusy(null);
    }
  }

  const editing = view.kind === "edit" ? view.list : null;
  const dirty = Boolean(editing && draft && draftDirty(draft, editing)) || (view.kind === "create" && name.trim().length > 0);
  const title = view.kind === "create" ? (view.fromBasket ? "Save basket as list" : "New list") : editing ? "Edit list" : "My lists";

  return <>
    <InputDialog
      open={open && !deleting}
      title={title}
      description={view.kind === "lists" ? "Lists you saved. Add one to your basket in one tap; you'll see the bill before anything is ordered." : view.kind === "create" && view.fromBasket ? "Name this list. It keeps the items and quantities in your basket now." : undefined}
      dirty={dirty}
      busy={busy === "create" || busy === "save" || busy === "delete"}
      discardMessage="Discard your changes to this list?"
      closeLabel="Close my lists"
      widthClassName="sm:max-w-md"
      onClose={onClose}
      footer={() => view.kind === "lists"
        ? <div className="grid grid-cols-2 gap-2">
          <button type="button" onClick={() => { setError(""); setView({ kind: "create", fromBasket: false }); }} className={`min-h-11 rounded-full border border-accent-border px-3 text-sm font-semibold text-ink-secondary transition hover:bg-surface-muted active:scale-[.98] ${ring}`}>New list</button>
          <button type="button" disabled={!basketStores.length} onClick={() => { setError(""); setView({ kind: "create", fromBasket: true }); }} className={`min-h-11 rounded-full bg-inverse px-3 text-sm font-semibold text-on-inverse transition hover:bg-inverse-hover active:scale-[.98] disabled:opacity-45 ${ring}`}>Save basket as list</button>
        </div>
        : <div className="flex items-center gap-3">
          <button type="button" disabled={Boolean(busy)} onClick={() => { if (dirty && !window.confirm("Discard your changes to this list?")) return; setError(""); setDraft(null); setView({ kind: "lists" }); }} className={`rounded-full px-4 py-3 text-sm font-semibold text-ink-secondary transition hover:bg-surface-strong disabled:opacity-40 ${ring}`}>Back</button>
          <button type="submit" form={formId} disabled={Boolean(busy)} className={`ml-auto flex flex-1 items-center justify-center gap-1.5 rounded-full bg-inverse px-5 py-3 text-sm font-semibold text-on-inverse transition active:scale-[.98] disabled:opacity-50 sm:flex-none ${ring}`}>{(busy === "create" || busy === "save") && <CircleNotch size={15} className="animate-spin" />}{view.kind === "create" ? (busy === "create" ? "Saving…" : "Save list") : busy === "save" ? "Saving…" : "Save"}</button>
        </div>}
    >
      {view.kind === "lists" && <div>
        {lists === null && !loadError && <div className="space-y-2" aria-label="Loading lists">{[0, 1].map((key) => <div key={key} className="h-16 animate-pulse rounded-2xl bg-surface-muted motion-reduce:animate-none" />)}</div>}
        {loadError && <div role="alert" className="rounded-xl bg-danger-soft px-3 py-2.5 text-xs leading-5 text-danger-hover">{loadError} <button type="button" onClick={() => void load()} className={`ml-1 font-semibold underline ${ring}`}>Try again</button></div>}
        {lists && !lists.length && <div className="flex flex-col items-center px-2 py-8 text-center">
          <span className="grid size-12 place-items-center rounded-2xl bg-surface-muted text-accent-ink"><ListChecks size={22} weight="duotone" /></span>
          <p className="mt-3 text-sm font-semibold text-ink">No lists yet</p>
          <p className="mt-1 max-w-[30ch] text-[13px] leading-5 text-muted">Save your basket as a list—like “Weekly list” or “Monthly ration”—to add it again in one tap.</p>
        </div>}
        {lists && lists.length > 0 && <ul className="divide-y divide-border">
          {lists.map((list) => <li key={list.id} className="flex items-center gap-2 py-3">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold text-ink">{list.name}</p>
              <p className="mt-0.5 truncate text-xs text-muted">{list.items.length ? list.items.map((item) => item.name).join(", ") : "Empty — add items to it"}</p>
            </div>
            <button type="button" onClick={() => { setError(""); setDraft(listDraft(list)); setView({ kind: "edit", list }); }} aria-label={`Edit ${list.name}`} className={`grid size-10 shrink-0 place-items-center rounded-full text-muted transition hover:bg-surface-strong active:scale-[.96] ${ring}`}><PencilSimple size={17} /></button>
            <button type="button" disabled={!list.items.length || addingListId !== null} onClick={() => onAddList(list)} aria-label={`Add ${list.name} to basket`} className={`flex h-10 shrink-0 items-center gap-1.5 rounded-full bg-accent px-3.5 text-xs font-semibold text-on-accent transition hover:bg-accent-hover active:scale-[.97] disabled:opacity-45 ${ring}`}>{addingListId === list.id ? <CircleNotch size={14} className="animate-spin" /> : <ShoppingCartSimple size={14} weight="bold" />}<span>{addingListId === list.id ? "Adding…" : "Add to basket"}</span></button>
          </li>)}
        </ul>}
      </div>}

      {view.kind === "create" && <form id={formId} onSubmit={(event) => void create(event)} className="space-y-3">
        <div>
          <label htmlFor={nameId} className="block text-xs font-semibold text-ink-secondary">List name</label>
          <input id={nameId} value={name} maxLength={60} autoComplete="off" placeholder="Weekly list" disabled={busy === "create"} onChange={(event) => { setName(event.target.value); setError(""); }} className="mt-1 min-h-11 w-full rounded-xl border border-border-strong bg-white px-3 text-sm outline-none placeholder:text-subtle focus:border-accent focus-visible:ring-2 focus-visible:ring-focus/30 disabled:opacity-60" />
        </div>
        {view.fromBasket ? (basketStores.length
          ? <p className="text-xs leading-5 text-muted">Saves the items in your {basketStores.map((store) => store === "zepto" ? "Zepto" : "Instamart").join(" and ")} basket.</p>
          : <p className="text-xs leading-5 text-muted">Your basket is empty. The list will start empty; add items to it later.</p>)
          : <p className="text-xs leading-5 text-muted">Starts empty. Fill your basket, then open the list and tap “Add basket items”.</p>}
        {error && <p role="alert" className="rounded-xl bg-danger-soft px-3 py-2 text-xs leading-5 text-danger-hover">{error}</p>}
      </form>}

      {editing && draft && <form id={formId} onSubmit={(event) => void save(event)} aria-label={`Edit ${editing.name}`} className="space-y-4">
        <div>
          <label htmlFor={nameId} className="block text-xs font-semibold text-ink-secondary">List name</label>
          <input id={nameId} value={draft.name} maxLength={60} autoComplete="off" disabled={busy === "save"} onChange={(event) => setDraft(draftRename(draft, event.target.value))} className="mt-1 min-h-11 w-full rounded-xl border border-border-strong bg-white px-3 text-sm outline-none focus:border-accent focus-visible:ring-2 focus-visible:ring-focus/30 disabled:opacity-60" />
        </div>
        {draft.items.length ? <ul className="divide-y divide-border rounded-2xl border border-border" aria-label="Items in this list">
          {draft.items.map((item, index) => <li key={`${item.ref.provider}:${item.ref.variant_id}`} className="flex items-center gap-2 px-3 py-2.5">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-ink">{item.name}</p>
              <p className="truncate text-[11px] text-muted">{[item.pack_size, item.store, item.last_price ? `last ${item.last_price.display}` : null].filter(Boolean).join(" · ")}</p>
            </div>
            <div className="flex h-9 items-center rounded-full border border-border-strong bg-white" role="group" aria-label={`Quantity of ${item.name}`}>
              <button type="button" onClick={() => setDraft(draftStep(draft, index, -1))} disabled={item.quantity <= 1} aria-label={`Decrease ${item.name}`} className={`grid size-9 place-items-center rounded-full text-ink-secondary transition hover:bg-accent-soft disabled:opacity-40 ${ring}`}><Minus size={13} weight="bold" /></button>
              <span className="min-w-6 text-center text-xs font-semibold tabular-nums" aria-live="polite">{item.quantity}</span>
              <button type="button" onClick={() => setDraft(draftStep(draft, index, 1))} disabled={item.quantity >= 50} aria-label={`Increase ${item.name}`} className={`grid size-9 place-items-center rounded-full text-ink-secondary transition hover:bg-accent-soft disabled:opacity-40 ${ring}`}><Plus size={13} weight="bold" /></button>
            </div>
            <button type="button" onClick={() => setDraft(draftRemove(draft, index))} aria-label={`Remove ${item.name} from the list`} className={`grid size-9 shrink-0 place-items-center rounded-full text-subtle transition hover:bg-danger-soft hover:text-danger ${ring}`}><Trash size={15} /></button>
          </li>)}
        </ul> : <p className="rounded-2xl bg-surface-muted px-3.5 py-3 text-xs leading-5 text-muted">No items yet.</p>}
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => void addBasketToList()} disabled={!basketStores.length || Boolean(busy)} className={`flex min-h-10 items-center gap-1.5 rounded-full border border-accent-border bg-white px-3.5 text-xs font-semibold text-accent-strong transition hover:bg-canvas active:scale-[.98] disabled:opacity-45 ${ring}`}>{busy === "add-basket" ? <CircleNotch size={14} className="animate-spin" /> : <Plus size={14} weight="bold" />}Add basket items</button>
          <button type="button" onClick={() => setDeleting(editing)} disabled={Boolean(busy)} className={`ml-auto rounded-full px-3 py-2 text-xs font-semibold text-danger-hover transition hover:bg-danger-soft disabled:opacity-45 ${ring}`}>Delete list</button>
        </div>
        {!basketStores.length && <p className="text-[11px] leading-4 text-muted">“Add basket items” works when your basket has something in it.</p>}
        {error && <p role="alert" className="rounded-xl bg-danger-soft px-3 py-2 text-xs leading-5 text-danger-hover">{error}</p>}
      </form>}
    </InputDialog>
    <ConfirmDialog
      open={deleting !== null}
      tone="destructive"
      title={`Delete “${deleting?.name ?? ""}”?`}
      message="The list and its items are removed. Your basket doesn't change."
      confirmLabel="Delete list"
      busyLabel="Deleting…"
      busy={busy === "delete"}
      onConfirm={() => void remove()}
      onClose={() => setDeleting(null)}
    />
  </>;
}
