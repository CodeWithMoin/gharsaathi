"use client";

import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  ArrowLeft,
  Brain,
  CaretRight,
  CircleNotch,
  MapPin,
  PencilSimple,
  Phone,
  PhoneIncoming,
  Storefront,
  Translate,
  X,
} from "@phosphor-icons/react";
import Image from "next/image";
import { DRAWER_PANEL_TRANSITION } from "./drawer-push.mjs";
import { ADDRESS_FORM_OPTIONS } from "./onboarding-flow.mjs";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useApiRequest } from "./authenticated-api";
import { MemorySettings } from "./memory-settings";
import { NotificationSwitch, type NotificationsController } from "./notification-settings";
import { profileChanges, profileDraft, type ProfileUpdate, type ProfileValues } from "./input-flows.mjs";
import { InputDialog } from "./ui/input-dialog";
import { RiderAssistSettings } from "./rider-assist-settings";
import { deliveryLineSettingsView, type DeliveryLineConfig } from "./delivery-line-settings.mjs";
import { MEMORY_TERM, SETTINGS_DRAWER_CLASS, sectionHeading, settingsRows, type SettingsRowId, type SettingsSection } from "./settings-map.mjs";

export type { SettingsSection } from "./settings-map.mjs";

export type AccountSettingsStore = {
  id: string;
  name: string;
  connected: boolean;
  /** A provider-specific status such as "Address ready" or "Needs reconnect". */
  detail?: string;
};

export type AccountSettingsIdentity = {
  name: string;
  email?: string;
  /** Use an image only when it is already trusted by the calling app. */
  imageUrl?: string;
};

export type AccountSettingsSheetProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  identity: AccountSettingsIdentity;
  stores?: AccountSettingsStore[];
  /** This is deliberately surfaced inside Stores, never in the account header. */
  commerceMode?: "demo" | "live";
  languageLabel?: string;
  /** Starts the store's own OAuth login (readiness is checked first); never opens another sheet. */
  onConnectStore?: (storeId: string) => void;
  /** Opens the Disconnect confirmation popup for a connected store. */
  onDisconnectStore?: (storeId: string) => void;
  /** Opens the "Choose delivery address" popup for one connected store. */
  onChooseStoreAddress?: (storeId: string) => void;
  /** Store whose login is being prepared, and the last connection problem, shown on Stores. */
  connectingStoreId?: string | null;
  storeError?: string;
  onLanguageChange?: (language: "hinglish" | "hindi" | "english") => void;
  /** Sarvam delivery line config from the page (`undefined` while loading, `null` if unreadable). */
  deliveryLine?: DeliveryLineConfig | null;
  /** Delivery line → "Directions for this address". */
  onOpenDeliveryDirections?: () => void;
  /** Show the parked Vobiz Rider assist row; only when the server reports it on. */
  riderAssistAvailable?: boolean;
  onSignOut: () => void;
  signOutPending?: boolean;
  /** Browser notifications; the page owns the controller so the order prompt shares it. */
  notifications?: NotificationsController;
  /** Open straight on one section, e.g. "memory" after "what do you know about me?". */
  initialSection?: SettingsSection;
};

/**
 * The right push drawer: a profile header row, Stores, What GharSaathi knows and
 * Delivery line (Beta), then a Sign out link. One width on every page
 * (`SETTINGS_DRAWER_CLASS`) and one detail layer, so it never becomes a settings maze.
 */
export function AccountSettingsSheet({
  open,
  onOpenChange,
  identity,
  stores = [],
  commerceMode = "demo",
  languageLabel = "Hinglish",
  onConnectStore,
  onDisconnectStore,
  onChooseStoreAddress,
  connectingStoreId = null,
  storeError = "",
  onLanguageChange,
  deliveryLine,
  onOpenDeliveryDirections,
  riderAssistAvailable = false,
  onSignOut,
  signOutPending = false,
  notifications,
  initialSection = null,
}: AccountSettingsSheetProps) {
  const [section, setSection] = useState<SettingsSection>(null);
  // Focus moves into the drawer itself on open (not the close button), so no focus ring shows
  // after a tap; keyboard users Tab to Back/Close and still get their focus-visible ring.
  const panelRef = useRef<HTMLElement>(null);
  const titleId = useId();
  const reducedMotion = useReducedMotion() ?? false;
  const connectedStoreCount = stores.filter((store) => store.connected).length;

  useEffect(() => {
    if (open && initialSection) setSection(initialSection);
  }, [open, initialSection]);

  useEffect(() => {
    if (!open) {
      setSection(null);
      return;
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusTimer = window.setTimeout(() => panelRef.current?.focus({ preventScroll: true }), 0);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onOpenChange(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.clearTimeout(focusTimer);
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [onOpenChange, open]);

  const close = () => onOpenChange(false);
  const heading = sectionHeading(section);
  const storeConnected = (id: string) => stores.some((store) => store.id === id && store.connected);
  const rows = settingsRows({ riderAssist: riderAssistAvailable });
  const line = deliveryLineSettingsView(deliveryLine);
  const rowDetails: Record<SettingsRowId, { icon: ReactNode; subtitle: string }> = {
    stores: { icon: <Storefront size={21} weight="duotone" />, subtitle: connectedStoreCount ? `${connectedStoreCount} store${connectedStoreCount === 1 ? "" : "s"} connected` : "Connect a shopping store" },
    memory: { icon: <Brain size={21} weight="duotone" />, subtitle: `${MEMORY_TERM}, order history, privacy` },
    delivery: { icon: <PhoneIncoming size={21} weight="duotone" />, subtitle: line.ready ? `On · ${line.display}` : "An AI answers delivery calls you decline" },
    rider: { icon: <Phone size={21} weight="duotone" />, subtitle: "Server-enabled rider calls" },
  };

  // A right-side push drawer: the page shell in page.tsx slides left by the same width with
  // the same spring and owns the backdrop, so tapping the pushed page closes this panel.
  return <AnimatePresence>
    {open && <motion.section
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`fixed inset-y-0 right-0 z-[35] flex h-[100dvh] ${SETTINGS_DRAWER_CLASS} flex-col overflow-hidden bg-surface-muted text-ink outline-none`}
        initial={reducedMotion ? { opacity: 0 } : { x: "100%" }}
        animate={{ x: 0, opacity: 1 }}
        exit={reducedMotion ? { opacity: 0 } : { x: "100%" }}
        transition={DRAWER_PANEL_TRANSITION}
      >
        <header className="flex items-center justify-between px-4 pb-3 pt-[max(1rem,env(safe-area-inset-top))]">
          {section ? <button type="button" onClick={() => setSection(null)} className="grid size-11 place-items-center rounded-full text-ink-secondary transition hover:bg-surface-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus" aria-label="Back to account settings"><ArrowLeft size={20} weight="bold" /></button> : <span className="size-11" />}
          <h2 id={titleId} className="font-display text-lg font-semibold tracking-[-.04em]">{heading}</h2>
          <button type="button" onClick={close} className="grid size-11 place-items-center rounded-full text-ink-secondary transition hover:bg-surface-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus" aria-label="Close account settings"><X size={20} weight="bold" /></button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-[max(1.5rem,env(safe-area-inset-bottom))]">
          <AnimatePresence mode="wait" initial={false}>
            {section === null
              ? <motion.div key="overview" initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -10 }} transition={{ duration: .16 }}>
                <button type="button" onClick={() => setSection("profile")} aria-label={`Profile: ${identity.name}`} className="flex min-h-[76px] w-full items-center gap-3 rounded-[22px] border border-border bg-surface p-3.5 text-left transition hover:bg-canvas focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus">
                  {identity.imageUrl ? <Image src={identity.imageUrl} alt="" width={48} height={48} className="size-12 rounded-2xl object-cover" /> : <span aria-hidden="true" className="grid size-12 shrink-0 place-items-center rounded-2xl bg-accent text-base font-bold text-on-accent">{initials(identity.name)}</span>}
                  <span className="min-w-0 flex-1"><span className="block truncate text-sm font-semibold">{identity.name}</span><span className="mt-0.5 block truncate text-xs text-muted">{identity.email ?? `Profile · ${languageLabel}`}</span></span>
                  <CaretRight size={17} className="shrink-0 text-subtle" weight="bold" />
                </button>

                <nav aria-label="Account settings" className="mt-4 overflow-hidden rounded-[22px] border border-border bg-surface">
                  {rows.map((row, index) => <SettingsRow
                    key={row.id}
                    icon={rowDetails[row.id].icon}
                    title={row.title}
                    subtitle={rowDetails[row.id].subtitle}
                    onClick={() => setSection(row.section)}
                    last={index === rows.length - 1}
                  />)}
                </nav>

                <div className="mt-5 flex justify-center">
                  <button type="button" onClick={onSignOut} disabled={signOutPending} className="flex min-h-11 items-center gap-1.5 rounded-xl px-3 text-sm font-semibold text-danger underline-offset-4 transition hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:cursor-not-allowed disabled:opacity-55">
                    {signOutPending && <CircleNotch size={15} className="animate-spin" />}{signOutPending ? "Signing out…" : "Sign out"}
                  </button>
                </div>
              </motion.div>
              : <motion.div key={section} initial={{ opacity: 0, x: 14 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 14 }} transition={{ duration: .16 }}>
                {section === "profile" && <div className="space-y-4"><ProfileDetail languageLabel={languageLabel} onLanguageChange={onLanguageChange} />{notifications && <NotificationSwitch controller={notifications} />}</div>}
                {section === "stores" && <StoresDetail stores={stores} commerceMode={commerceMode} connectingStoreId={connectingStoreId} error={storeError} onConnectStore={onConnectStore} onDisconnectStore={onDisconnectStore} onChooseStoreAddress={onChooseStoreAddress} />}
                {section === "memory" && <MemorySettings onEditProfile={() => setSection("profile")} commerceMode={commerceMode} swiggyConnected={storeConnected("swiggy")} zeptoConnected={storeConnected("zepto")} />}
                {section === "delivery" && <DeliveryLineDetail config={deliveryLine} onOpenDirections={onOpenDeliveryDirections} />}
                {section === "rider" && riderAssistAvailable && <RiderAssistSettings />}
              </motion.div>}
          </AnimatePresence>
        </div>
      </motion.section>}
  </AnimatePresence>;
}

/** A 44px+ account trigger whose fallback label is the requested M-style initial. */
export function AccountMenuButton({ name, onClick, imageUrl, active = false }: { name: string; onClick: () => void; imageUrl?: string; active?: boolean }) {
  return <button type="button" onClick={onClick} aria-label="Open account settings" aria-expanded={active} className="grid size-11 shrink-0 place-items-center overflow-hidden rounded-full bg-accent text-sm font-bold text-on-accent shadow-[0_12px_26px_-20px_rgb(var(--accent-shadow)/.8)] transition-[background-color,transform] duration-150 ease-out hover:bg-accent-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-surface active:scale-[.96] motion-reduce:active:scale-100">
    {imageUrl ? <Image src={imageUrl} alt="" width={44} height={44} className="size-full object-cover" /> : initials(name)}
  </button>;
}

function SettingsRow({ icon, title, subtitle, onClick, last = false }: { icon: ReactNode; title: string; subtitle: string; onClick: () => void; last?: boolean }) {
  return <button type="button" onClick={onClick} className={`flex min-h-[72px] w-full items-center gap-3 px-3.5 text-left transition hover:bg-canvas focus-visible:relative focus-visible:z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus ${last ? "" : "border-b border-border"}`}>
    <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent-hover">{icon}</span><span className="min-w-0 flex-1"><span className="block text-sm font-semibold">{title}</span><span className="mt-0.5 block truncate text-xs text-muted">{subtitle}</span></span><CaretRight size={17} className="shrink-0 text-subtle" weight="bold" />
  </button>;
}

function StoresDetail({ stores, commerceMode, connectingStoreId, error, onConnectStore, onDisconnectStore, onChooseStoreAddress }: { stores: AccountSettingsStore[]; commerceMode: "demo" | "live"; connectingStoreId: string | null; error: string; onConnectStore?: (storeId: string) => void; onDisconnectStore?: (storeId: string) => void; onChooseStoreAddress?: (storeId: string) => void }) {
  const storeAction = (store: AccountSettingsStore) => {
    if (commerceMode === "demo") return null;
    if (store.connected) return <button type="button" onClick={() => onDisconnectStore?.(store.id)} disabled={!onDisconnectStore} className="min-h-10 rounded-xl border border-accent-border px-3 text-xs font-semibold text-ink-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-55">Disconnect</button>;
    const connecting = connectingStoreId === store.id;
    return <button type="button" onClick={() => onConnectStore?.(store.id)} disabled={!onConnectStore || connecting} className="flex min-h-10 items-center gap-1.5 rounded-xl bg-accent px-3 text-xs font-semibold text-on-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-60">{connecting && <CircleNotch size={13} className="animate-spin" />}{connecting ? "Checking…" : "Connect"}</button>;
  };
  return <div>
    <div className="rounded-2xl border border-border bg-surface p-4"><p className="text-[11px] font-bold uppercase tracking-[.13em] text-muted">Shopping mode</p><div className="mt-2 flex items-center gap-2"><span className={`size-2 rounded-full ${commerceMode === "live" ? "bg-success" : "bg-warning"}`} /><p className="text-sm font-semibold">{commerceMode === "live" ? "Live shopping" : "Safe demo mode"}</p></div><p className="mt-1 text-xs leading-5 text-muted">{commerceMode === "live" ? "Searches use your connected stores. Purchases still need your approval." : "Try the experience without provider calls or store changes."}</p></div>
    <div className="mt-4 overflow-hidden rounded-2xl border border-border bg-surface">{stores.length ? stores.map((store, index) => <div key={store.id} className={index ? "border-t border-border" : ""}>
      <div className="flex min-h-[76px] items-center gap-3 px-3.5"><span className="grid size-10 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent-hover"><Storefront size={21} weight="duotone" /></span><span className="min-w-0 flex-1"><span className="block text-sm font-semibold">{store.name}</span><span className="mt-0.5 block truncate text-xs text-muted">{store.detail ?? (store.connected ? "Connected" : "Not connected")}</span></span>{storeAction(store)}</div>
      {store.connected && onChooseStoreAddress && <button type="button" onClick={() => onChooseStoreAddress(store.id)} className="flex min-h-11 w-full items-center gap-2 border-t border-border px-3.5 text-left text-xs font-semibold text-accent-ink transition-colors hover:bg-canvas focus-visible:relative focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus"><MapPin size={15} weight="bold" className="shrink-0" /><span className="min-w-0 flex-1 truncate">Change {store.name} address</span><CaretRight size={14} weight="bold" className="shrink-0 text-subtle" /></button>}
    </div>) : <p className="px-4 py-5 text-sm leading-6 text-muted">No stores are connected yet. Connect one when you’re ready to search live catalogues.</p>}</div>
    {error && <p role="alert" className="mt-3 rounded-xl bg-danger-soft px-3 py-2 text-xs leading-5 text-danger-hover">{error}</p>}
    {commerceMode === "live" && <p className="mt-3 px-1 text-[11px] leading-4 text-muted">You log in on the store’s own page. GharSaathi never asks for your password or OTP.</p>}
  </div>;
}

type AddressForm = "masculine" | "feminine" | "neutral";
type ReplyLanguage = "hinglish" | "hindi" | "english";
type ReplyStyle = "quick" | "detailed";
type VoiceProfile = { first_name: string | null; address_form: AddressForm; language: ReplyLanguage; reply_style?: ReplyStyle };

const ADDRESS_FORMS: { id: AddressForm; label: string; example: string }[] = ADDRESS_FORM_OPTIONS.map((option) => ({ id: option.value, label: option.label, example: option.example }));
const LANGUAGES: { id: ReplyLanguage; label: string }[] = [
  { id: "hinglish", label: "Hinglish" },
  { id: "hindi", label: "Hindi" },
  { id: "english", label: "English" },
];
const REPLY_STYLES: { id: ReplyStyle; label: string; hint: string }[] = [
  { id: "quick", label: "Quick", hint: "One short line. Details stay on the cards." },
  { id: "detailed", label: "Detailed", hint: "A little more explanation, such as the main options and why." },
];
const choiceClass = (selected: boolean) => `min-h-11 rounded-xl px-2 text-xs font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-60 ${selected ? "bg-accent text-on-accent" : "border border-accent-border text-ink-secondary"}`;

function ProfileDetail({ languageLabel, onLanguageChange }: { languageLabel: string; onLanguageChange?: (language: "hinglish" | "hindi" | "english") => void }) {
  const request = useApiRequest();
  const [profile, setProfile] = useState<VoiceProfile | null>(null);
  const [loadError, setLoadError] = useState("");
  const [editing, setEditing] = useState(false);
  const [savedNotice, setSavedNotice] = useState(false);

  useEffect(() => {
    let active = true;
    void request<VoiceProfile>("/api/profile")
      .then((loaded) => { if (active) setProfile(loaded); })
      .catch(() => { if (active) { setProfile({ first_name: null, address_form: "neutral", language: "hinglish" }); setLoadError("Could not load your profile."); } });
    return () => { active = false; };
  }, [request]);

  const draft = profileDraft(profile, languageLabel);
  const addressForm = ADDRESS_FORMS.find((form) => form.id === draft.address_form);
  return <div className="rounded-2xl border border-border bg-surface p-4">
    <span className="grid size-11 place-items-center rounded-xl bg-accent-soft text-accent-hover"><Translate size={24} weight="duotone" /></span>
    <h3 className="mt-4 text-base font-semibold tracking-[-.02em]">Your profile</h3>
    <p className="mt-2 text-sm leading-6 text-muted">How GharSaathi speaks and writes with you. You can change it at any time.</p>
    {profile
      ? <dl className="mt-4 divide-y divide-border rounded-xl border border-border text-sm">
        <div className="flex items-center gap-3 px-3 py-2.5"><dt className="w-28 shrink-0 text-xs text-muted">Name</dt><dd className="min-w-0 flex-1 truncate font-semibold">{draft.first_name || "Not set"}</dd></div>
        <div className="flex items-center gap-3 px-3 py-2.5"><dt className="w-28 shrink-0 text-xs text-muted">How to address you</dt><dd className="min-w-0 flex-1"><span className="block font-semibold">{addressForm?.label}</span><span className="block truncate text-[11px] text-muted">“{addressForm?.example}”</span></dd></div>
        <div className="flex items-center gap-3 px-3 py-2.5"><dt className="w-28 shrink-0 text-xs text-muted">Reply language</dt><dd className="min-w-0 flex-1 font-semibold">{LANGUAGES.find((language) => language.id === draft.language)?.label}</dd></div>
      </dl>
      : <div className="mt-4 h-28 animate-pulse rounded-xl bg-surface-strong" aria-label="Loading profile" />}
    <p aria-live="polite" className={`mt-2 min-h-4 text-[11px] ${loadError ? "text-danger" : "text-success"}`}>{loadError || (savedNotice ? "Saved. This applies from your next voice chat." : "")}</p>
    <button type="button" onClick={() => { setSavedNotice(false); setEditing(true); }} disabled={!profile} className="mt-2 flex min-h-11 w-full items-center justify-center gap-2 rounded-xl bg-inverse px-4 text-sm font-semibold text-on-inverse focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-50"><PencilSimple size={16} weight="bold" />Edit profile</button>
    {profile && <ReplyStyleSetting
      value={profile.reply_style ?? "quick"}
      onChange={async (reply_style) => {
        const saved = await request<VoiceProfile>("/api/profile", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reply_style }) });
        setProfile(saved);
        setLoadError("");
        setSavedNotice(true);
      }}
    />}
    <EditProfileDialog
      open={editing && profile !== null}
      initial={draft}
      languageEditable={Boolean(onLanguageChange)}
      onClose={() => setEditing(false)}
      onSave={async (changes) => {
        const saved = await request<VoiceProfile>("/api/profile", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(changes) });
        setProfile(saved);
        setLoadError("");
        if (changes.language) onLanguageChange?.(changes.language);
        setSavedNotice(true);
        setEditing(false);
      }}
    />
  </div>;
}

/** Quick (default) or detailed replies; saved as soon as it is tapped. */
function ReplyStyleSetting({ value, onChange }: { value: ReplyStyle; onChange: (style: ReplyStyle) => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const hint = REPLY_STYLES.find((style) => style.id === value)?.hint;
  return <fieldset className="mt-3">
    <legend className="text-xs font-semibold text-ink-secondary">How much should GharSaathi say?</legend>
    <div className="mt-1.5 grid grid-cols-2 gap-2">
      {REPLY_STYLES.map((style) => <button key={style.id} type="button" aria-pressed={value === style.id} disabled={busy} onClick={async () => {
        if (style.id === value) return;
        setBusy(true);
        setError("");
        try { await onChange(style.id); } catch { setError("Could not save. Try again."); } finally { setBusy(false); }
      }} className={choiceClass(value === style.id)}>{style.label}</button>)}
    </div>
    <p aria-live="polite" className={`mt-2 text-[11px] ${error ? "text-danger" : "text-muted"}`}>{error || hint}</p>
  </fieldset>;
}

/** One "Edit profile" popup for name, how to address you and reply language; one Save. */
function EditProfileDialog({ open, initial, languageEditable, onClose, onSave }: { open: boolean; initial: ProfileValues; languageEditable: boolean; onClose: () => void; onSave: (changes: ProfileUpdate) => Promise<void> }) {
  const baseId = useId();
  const formId = `${baseId}-form`;
  const [session, setSession] = useState<{ initial: ProfileValues; values: ProfileValues } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (open && !session) setSession({ initial, values: initial });
  else if (!open && session && !busy) setSession(null);
  const values = session?.values ?? initial;
  const dirty = open && session !== null && profileChanges(session.initial, session.values) !== null;
  const update = (patch: Partial<ProfileValues>) => { setError(""); setSession((current) => current && { ...current, values: { ...current.values, ...patch } }); };

  async function submit() {
    if (!session || busy) return;
    const changes = profileChanges(session.initial, session.values);
    if (!changes) { onClose(); return; }
    setBusy(true);
    setError("");
    try {
      await onSave(changes);
    } catch (cause) {
      setError(cause instanceof Error && cause.message !== "Request failed." ? cause.message : "Could not save. Use only letters in your name.");
    } finally {
      setBusy(false);
    }
  }

  const example = ADDRESS_FORMS.find((form) => form.id === values.address_form)?.example;
  return <InputDialog
    open={open}
    title="Edit profile"
    description="GharSaathi uses this when talking to you."
    dirty={dirty}
    busy={busy}
    discardMessage="Discard your profile changes?"
    closeLabel="Close edit profile"
    widthClassName="sm:max-w-md"
    onClose={() => { setError(""); onClose(); }}
    footer={({ requestClose }) => <div className="flex items-center gap-3">
      <button type="button" disabled={busy} onClick={() => requestClose("cancel")} className="rounded-full px-4 py-3 text-sm font-semibold text-ink-secondary transition hover:bg-surface-strong disabled:opacity-40">Cancel</button>
      <button type="submit" form={formId} disabled={busy} className="ml-auto flex flex-1 items-center justify-center gap-1.5 rounded-full bg-inverse px-5 py-3 text-sm font-semibold text-on-inverse disabled:opacity-50 sm:flex-none">{busy && <CircleNotch size={15} className="animate-spin" />}{busy ? "Saving…" : "Save"}</button>
    </div>}
  >
    <form id={formId} onSubmit={(event) => { event.preventDefault(); void submit(); }} className="space-y-5">
      <div>
        <label htmlFor={`${baseId}-name`} className="block text-xs font-semibold text-ink-secondary">What should GharSaathi call you? (optional)</label>
        <input id={`${baseId}-name`} value={values.first_name} maxLength={40} disabled={busy} onChange={(event) => update({ first_name: event.target.value })} placeholder="e.g. Moin" autoComplete="given-name" className="mt-1.5 min-h-11 w-full rounded-xl border border-border-strong bg-white px-3 text-sm outline-none focus:border-accent focus-visible:ring-2 focus-visible:ring-focus/30" />
      </div>
      <fieldset>
        <legend className="text-xs font-semibold text-ink-secondary">How should GharSaathi address you?</legend>
        <div className="mt-1.5 grid grid-cols-3 gap-2">
          {ADDRESS_FORMS.map((form) => <button key={form.id} type="button" aria-pressed={values.address_form === form.id} disabled={busy} onClick={() => update({ address_form: form.id })} className={choiceClass(values.address_form === form.id)}>{form.label}</button>)}
        </div>
        <p className="mt-2 text-[11px] text-muted">For example: “{example}”</p>
      </fieldset>
      <fieldset>
        <legend className="text-xs font-semibold text-ink-secondary">Reply language</legend>
        <div className="mt-1.5 grid grid-cols-3 gap-2">
          {LANGUAGES.map((language) => <button key={language.id} type="button" aria-pressed={values.language === language.id} disabled={busy || !languageEditable} onClick={() => update({ language: language.id })} className={choiceClass(values.language === language.id)}>{language.label}</button>)}
        </div>
      </fieldset>
      {error && <p role="alert" className="rounded-xl bg-danger-soft px-3 py-2 text-xs leading-5 text-danger-hover">{error}</p>}
    </form>
  </InputDialog>;
}

/**
 * Delivery line (Beta): the Sarvam AI number answers delivery calls the owner declines.
 * Forwarding is "when busy" only; forwarding unanswered or unreachable calls would loop
 * when the AI transfers the rider back.
 */
function DeliveryLineDetail({ config, onOpenDirections }: { config: DeliveryLineConfig | null | undefined; onOpenDirections?: () => void }) {
  const line = deliveryLineSettingsView(config);
  const code = "rounded-md bg-surface-muted px-1.5 py-0.5 font-mono text-[12px] font-semibold text-ink";
  return <div className="space-y-4">
    <div className="rounded-2xl border border-border bg-surface p-4">
      <span className="grid size-11 place-items-center rounded-xl bg-accent-soft text-accent-hover"><PhoneIncoming size={24} weight="duotone" /></span>
      <p className="mt-4 text-sm leading-6 text-ink-secondary">When a delivery rider calls and you decline, an AI answers for you. It guides the rider step by step using your saved directions. It never shares OTPs or payment details.</p>
      <dl className="mt-4 divide-y divide-border rounded-xl border border-border text-sm">
        <div className="flex items-center gap-3 px-3 py-2.5"><dt className="w-24 shrink-0 text-xs text-muted">AI number</dt><dd className="min-w-0 flex-1 font-semibold tabular-nums">{line.display}</dd></div>
        <div className="flex items-center gap-3 px-3 py-2.5"><dt className="w-24 shrink-0 text-xs text-muted">Status</dt><dd className="flex min-w-0 flex-1 items-center gap-2 font-semibold"><span aria-hidden="true" className={`size-2 shrink-0 rounded-full ${line.ready ? "bg-success" : config === undefined ? "bg-border-strong" : "bg-warning"}`} />{line.status}</dd></div>
      </dl>
    </div>

    <section aria-labelledby="delivery-line-how" className="rounded-2xl border border-border bg-surface p-4">
      <h3 id="delivery-line-how" className="text-sm font-semibold">How to use</h3>
      <p className="mt-1.5 text-xs leading-5 text-muted">Decline the rider’s call (or press the side button twice). The AI picks it up.</p>
      <h3 className="mt-4 text-sm font-semibold">Forwarding: when busy only</h3>
      <p className="mt-1.5 text-xs leading-6 text-muted">Dial <span className={code}>{line.forwarding}</span> on your phone once. Turn it off with <span className={code}>{line.off}</span>.</p>
      <p className="mt-1.5 text-[11px] leading-4 text-muted">Don’t forward unanswered or unreachable calls; the AI would loop when it hands the rider back to you.</p>
    </section>

    <button type="button" onClick={onOpenDirections} disabled={!onOpenDirections} className="flex min-h-11 w-full items-center justify-center gap-2 rounded-xl bg-inverse px-4 text-sm font-semibold text-on-inverse transition-transform active:scale-[.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-50 motion-reduce:active:scale-100"><MapPin size={16} weight="bold" />Directions for this address</button>
    <p className="-mt-2 px-1 text-[11px] leading-4 text-muted">Landmark, step-by-step route, gate note and handover for your selected address.</p>
  </div>;
}

function initials(name: string) {
  return name.trim().split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase() || "M";
}
