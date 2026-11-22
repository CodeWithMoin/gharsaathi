"use client";

import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowLeft,
  Brain,
  CaretRight,
  Check,
  CircleNotch,
  Eye,
  MapPin,
  PencilSimple,
  ShieldCheck,
  SignOut,
  Storefront,
  Translate,
  X,
} from "@phosphor-icons/react";
import Image from "next/image";
import { ADDRESS_FORM_OPTIONS } from "./onboarding-flow.mjs";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useApiRequest } from "./authenticated-api";
import { MemorySettings } from "./memory-settings";
import { profileChanges, profileDraft, type ProfileUpdate, type ProfileValues } from "./input-flows.mjs";
import { InputDialog } from "./ui/input-dialog";

export type AccountSettingsStore = {
  id: string;
  name: string;
  connected: boolean;
  /** A provider-specific status such as "Address ready" or "Needs reconnect". */
  detail?: string;
};

export type AccountSettingsAddress = {
  id: string;
  label: string;
  detail?: string;
  selected?: boolean;
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
  privacySummary?: string;
  addresses?: AccountSettingsAddress[];
  onConnectStore?: (storeId: string) => void;
  onDisconnectStore?: (storeId: string) => void;
  onManagePreferences?: () => void;
  onLanguageChange?: (language: "hinglish" | "hindi" | "english") => void;
  onManagePrivacy?: () => void;
  onManageAddresses?: () => void;
  onSelectAddress?: (addressId: string) => void;
  /** Opens the "Choose delivery address" popup for one connected store. */
  onChooseStoreAddress?: (storeId: string) => void;
  onSignOut: () => void;
  signOutPending?: boolean;
};

type SettingsSection = "stores" | "preferences" | "memory" | "privacy" | "addresses" | null;

/**
 * A controlled, mobile-first account panel. It intentionally has only an overview
 * and one detail layer, so connected-store and address flows never become a settings maze.
 */
export function AccountSettingsSheet({
  open,
  onOpenChange,
  identity,
  stores = [],
  commerceMode = "demo",
  languageLabel = "Hinglish",
  privacySummary = "Memory on for choices you share",
  addresses = [],
  onConnectStore,
  onDisconnectStore,
  onManagePreferences,
  onLanguageChange,
  onManagePrivacy,
  onManageAddresses,
  onSelectAddress,
  onChooseStoreAddress,
  onSignOut,
  signOutPending = false,
}: AccountSettingsSheetProps) {
  const [section, setSection] = useState<SettingsSection>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const connectedStoreCount = stores.filter((store) => store.connected).length;
  const addressSummary = addresses.length === 0
    ? "Add a delivery address"
    : addresses.find((address) => address.selected)?.label ?? `${addresses.length} saved ${addresses.length === 1 ? "address" : "addresses"}`;

  useEffect(() => {
    if (!open) {
      setSection(null);
      return;
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusTimer = window.setTimeout(() => closeButtonRef.current?.focus(), 0);
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
  const heading = section === "stores"
    ? "Stores"
    : section === "preferences"
      ? "Preferences"
      : section === "memory"
        ? "Saved preferences"
        : section === "privacy"
          ? "Privacy & memory"
          : section === "addresses"
            ? "Addresses"
            : "Account";

  return <AnimatePresence>
    {open && <motion.div className="fixed inset-0 z-50 flex items-stretch justify-start" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
      <button type="button" aria-label="Close account settings" className="absolute inset-0 cursor-default bg-[#3a271e]/35 backdrop-blur-[2px]" onClick={close} />
      <motion.section
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`relative flex h-[100dvh] ${section === "memory" ? "w-[min(94vw,420px)]" : "w-[min(75vw,360px)]"} flex-col overflow-hidden rounded-r-[36px] border-r border-[#ead7ca] bg-[#fffaf5] text-[#35241c] shadow-[18px_0_55px_-36px_rgba(62,34,22,.52)] [corner-shape:squircle]`}
        initial={{ x: "-100%", opacity: 0.85 }}
        animate={{ x: 0, opacity: 1 }}
        exit={{ x: "-100%", opacity: 0.85 }}
        transition={{ type: "spring", damping: 28, stiffness: 330 }}
      >
        <header className="flex items-center justify-between px-4 pb-3 pt-[max(1rem,env(safe-area-inset-top))]">
          {section ? <button type="button" onClick={() => setSection(null)} className="grid size-11 place-items-center rounded-full text-[#70584b] transition hover:bg-[#f5eae2] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#c6531d]" aria-label="Back to account settings"><ArrowLeft size={20} weight="bold" /></button> : <span className="size-11" />}
          <h2 id={titleId} className="font-display text-lg font-semibold tracking-[-.04em]">{heading}</h2>
          <button ref={closeButtonRef} type="button" onClick={close} className="grid size-11 place-items-center rounded-full text-[#70584b] transition hover:bg-[#f5eae2] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#c6531d]" aria-label="Close account settings"><X size={20} weight="bold" /></button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-[max(1.5rem,env(safe-area-inset-bottom))]">
          <AnimatePresence mode="wait" initial={false}>
            {section === null
              ? <motion.div key="overview" initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -10 }} transition={{ duration: .16 }}>
                <div className="flex items-center gap-3 rounded-[22px] border border-[#ead9cd] bg-[#fffdf9] p-3.5">
                  {identity.imageUrl ? <Image src={identity.imageUrl} alt="" width={48} height={48} className="size-12 rounded-2xl object-cover" /> : <span aria-hidden="true" className="grid size-12 place-items-center rounded-2xl bg-[#f1ddd0] text-base font-bold text-[#93471f]">{initials(identity.name)}</span>}
                  <div className="min-w-0"><p className="truncate text-sm font-semibold">{identity.name}</p>{identity.email && <p className="mt-0.5 truncate text-xs text-[#8d766a]">{identity.email}</p>}</div>
                </div>

                <nav aria-label="Account settings" className="mt-4 overflow-hidden rounded-[22px] border border-[#ead9cd] bg-[#fffdf9]">
                  <SettingsRow icon={<Storefront size={21} weight="duotone" />} title="Stores" subtitle={connectedStoreCount ? `${connectedStoreCount} store${connectedStoreCount === 1 ? "" : "s"} connected` : "Connect a shopping store"} onClick={() => setSection("stores")} />
                  <SettingsRow icon={<Translate size={21} weight="duotone" />} title="Preferences" subtitle={languageLabel} onClick={() => setSection("preferences")} />
                  <SettingsRow icon={<Brain size={21} weight="duotone" />} title="Saved preferences" subtitle="Preferences, never, rules" onClick={() => setSection("memory")} />
                  <SettingsRow icon={<ShieldCheck size={21} weight="duotone" />} title="Privacy & memory" subtitle={privacySummary} onClick={() => setSection("privacy")} />
                  <SettingsRow icon={<MapPin size={21} weight="duotone" />} title="Addresses" subtitle={addressSummary} onClick={() => setSection("addresses")} last />
                </nav>

                <button type="button" onClick={onSignOut} disabled={signOutPending} className="mt-5 flex min-h-12 w-full items-center justify-center gap-2 rounded-2xl border border-[#e6cfc2] bg-[#fffdf9] px-4 text-sm font-semibold text-[#9f3f2c] transition hover:bg-[#fff5ef] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#c6531d] disabled:cursor-not-allowed disabled:opacity-55">
                  {signOutPending ? <CircleNotch size={18} className="animate-spin" /> : <SignOut size={18} weight="bold" />}{signOutPending ? "Signing out…" : "Sign out"}
                </button>
              </motion.div>
              : <motion.div key={section} initial={{ opacity: 0, x: 14 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 14 }} transition={{ duration: .16 }}>
                {section === "stores" && <StoresDetail stores={stores} commerceMode={commerceMode} onConnectStore={onConnectStore} onDisconnectStore={onDisconnectStore} />}
                {section === "preferences" && <PreferencesDetail languageLabel={languageLabel} onLanguageChange={onLanguageChange} onManagePreferences={onManagePreferences} />}
                {section === "memory" && <MemorySettings />}
                {section === "privacy" && <SimpleDetail icon={<Eye size={24} weight="duotone" />} title={privacySummary} description="You choose what GharSaathi remembers. Shopping history alone never becomes a stock prediction." action="Review privacy & memory" onAction={onManagePrivacy} />}
                {section === "addresses" && <AddressesDetail addresses={addresses} stores={stores} onSelectAddress={onSelectAddress} onManageAddresses={onManageAddresses} onChooseStoreAddress={onChooseStoreAddress} />}
              </motion.div>}
          </AnimatePresence>
        </div>
      </motion.section>
    </motion.div>}
  </AnimatePresence>;
}

/** A 44px+ account trigger whose fallback label is the requested M-style initial. */
export function AccountMenuButton({ name, onClick, imageUrl }: { name: string; onClick: () => void; imageUrl?: string }) {
  return <button type="button" onClick={onClick} aria-label="Open account settings" className="grid size-11 shrink-0 place-items-center overflow-hidden rounded-2xl border border-[#e4cabc] bg-[#fffaf5] text-sm font-bold text-[#93471f] shadow-[0_12px_26px_-20px_rgba(81,39,21,.8)] transition hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#c6531d] active:scale-[.96]">
    {imageUrl ? <Image src={imageUrl} alt="" width={44} height={44} className="size-full object-cover" /> : initials(name)}
  </button>;
}

function SettingsRow({ icon, title, subtitle, onClick, last = false }: { icon: ReactNode; title: string; subtitle: string; onClick: () => void; last?: boolean }) {
  return <button type="button" onClick={onClick} className={`flex min-h-[72px] w-full items-center gap-3 px-3.5 text-left transition hover:bg-[#fff8f3] focus-visible:relative focus-visible:z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#c6531d] ${last ? "" : "border-b border-[#f0e2d8]"}`}>
    <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-[#f7e8de] text-[#bd5525]">{icon}</span><span className="min-w-0 flex-1"><span className="block text-sm font-semibold">{title}</span><span className="mt-0.5 block truncate text-xs text-[#8d766a]">{subtitle}</span></span><CaretRight size={17} className="shrink-0 text-[#a48a7b]" weight="bold" />
  </button>;
}

function StoresDetail({ stores, commerceMode, onConnectStore, onDisconnectStore }: { stores: AccountSettingsStore[]; commerceMode: "demo" | "live"; onConnectStore?: (storeId: string) => void; onDisconnectStore?: (storeId: string) => void }) {
  return <div>
    <div className="rounded-2xl border border-[#ead9cd] bg-[#fffdf9] p-4"><p className="text-[11px] font-bold uppercase tracking-[.13em] text-[#9a7562]">Shopping mode</p><div className="mt-2 flex items-center gap-2"><span className={`size-2 rounded-full ${commerceMode === "live" ? "bg-[#3f8b65]" : "bg-[#ca7930]"}`} /><p className="text-sm font-semibold">{commerceMode === "live" ? "Live shopping" : "Safe demo mode"}</p></div><p className="mt-1 text-xs leading-5 text-[#806f65]">{commerceMode === "live" ? "Searches use your connected stores. Purchases still need your approval." : "Try the experience without provider calls or store changes."}</p></div>
    <div className="mt-4 overflow-hidden rounded-2xl border border-[#ead9cd] bg-[#fffdf9]">{stores.length ? stores.map((store, index) => <div key={store.id} className={`flex min-h-[76px] items-center gap-3 px-3.5 ${index ? "border-t border-[#f0e2d8]" : ""}`}><span className="grid size-10 place-items-center rounded-xl bg-[#f7e8de] text-[#bd5525]"><Storefront size={21} weight="duotone" /></span><span className="min-w-0 flex-1"><span className="block text-sm font-semibold">{store.name}</span><span className="mt-0.5 block truncate text-xs text-[#8d766a]">{store.detail ?? (store.connected ? "Connected" : "Not connected")}</span></span>{store.connected ? <button type="button" onClick={() => onDisconnectStore?.(store.id)} className="min-h-10 rounded-xl border border-[#e4cabc] px-3 text-xs font-semibold text-[#765549] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#c6531d]">Manage</button> : <button type="button" onClick={() => onConnectStore?.(store.id)} className="min-h-10 rounded-xl bg-[#c6531d] px-3 text-xs font-semibold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#c6531d]">Connect</button>}</div>) : <p className="px-4 py-5 text-sm leading-6 text-[#806f65]">No stores are connected yet. Connect one when you’re ready to search live catalogues.</p>}</div>
  </div>;
}

function SimpleDetail({ icon, title, description, action, onAction }: { icon: ReactNode; title: string; description: string; action: string; onAction?: () => void }) {
  return <div className="rounded-2xl border border-[#ead9cd] bg-[#fffdf9] p-4"><span className="grid size-11 place-items-center rounded-xl bg-[#f7e8de] text-[#bd5525]">{icon}</span><h3 className="mt-4 text-base font-semibold tracking-[-.02em]">{title}</h3><p className="mt-2 text-sm leading-6 text-[#806f65]">{description}</p><button type="button" onClick={onAction} disabled={!onAction} className="mt-5 min-h-11 w-full rounded-xl border border-[#e4cabc] px-4 text-sm font-semibold text-[#68493c] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#c6531d] disabled:cursor-default disabled:opacity-55">{action}</button></div>;
}

type AddressForm = "masculine" | "feminine" | "neutral";
type ReplyLanguage = "hinglish" | "hindi" | "english";
type VoiceProfile = { first_name: string | null; address_form: AddressForm; language: ReplyLanguage };

const ADDRESS_FORMS: { id: AddressForm; label: string; example: string }[] = ADDRESS_FORM_OPTIONS.map((option) => ({ id: option.value, label: option.label, example: option.example }));
const LANGUAGES: { id: ReplyLanguage; label: string }[] = [
  { id: "hinglish", label: "Hinglish" },
  { id: "hindi", label: "Hindi" },
  { id: "english", label: "English" },
];
const choiceClass = (selected: boolean) => `min-h-11 rounded-xl px-2 text-xs font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#c6531d] disabled:opacity-60 ${selected ? "bg-[#c6531d] text-white" : "border border-[#e4cabc] text-[#68493c]"}`;

function PreferencesDetail({ languageLabel, onLanguageChange, onManagePreferences }: { languageLabel: string; onLanguageChange?: (language: "hinglish" | "hindi" | "english") => void; onManagePreferences?: () => void }) {
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
  return <div className="rounded-2xl border border-[#ead9cd] bg-[#fffdf9] p-4">
    <span className="grid size-11 place-items-center rounded-xl bg-[#f7e8de] text-[#bd5525]"><Translate size={24} weight="duotone" /></span>
    <h3 className="mt-4 text-base font-semibold tracking-[-.02em]">Your profile</h3>
    <p className="mt-2 text-sm leading-6 text-[#806f65]">How GharSaathi speaks and writes with you. You can change it at any time.</p>
    {profile
      ? <dl className="mt-4 divide-y divide-[#f0e2d8] rounded-xl border border-[#f0e2d8] text-sm">
        <div className="flex items-center gap-3 px-3 py-2.5"><dt className="w-28 shrink-0 text-xs text-[#8d766a]">Name</dt><dd className="min-w-0 flex-1 truncate font-semibold">{draft.first_name || "Not set"}</dd></div>
        <div className="flex items-center gap-3 px-3 py-2.5"><dt className="w-28 shrink-0 text-xs text-[#8d766a]">How to address you</dt><dd className="min-w-0 flex-1"><span className="block font-semibold">{addressForm?.label}</span><span className="block truncate text-[11px] text-[#8d766a]">“{addressForm?.example}”</span></dd></div>
        <div className="flex items-center gap-3 px-3 py-2.5"><dt className="w-28 shrink-0 text-xs text-[#8d766a]">Reply language</dt><dd className="min-w-0 flex-1 font-semibold">{LANGUAGES.find((language) => language.id === draft.language)?.label}</dd></div>
      </dl>
      : <div className="mt-4 h-28 animate-pulse rounded-xl bg-[#f3e6dc]" aria-label="Loading profile" />}
    <p aria-live="polite" className={`mt-2 min-h-4 text-[11px] ${loadError ? "text-[#a33d2c]" : "text-[#55745c]"}`}>{loadError || (savedNotice ? "Saved. This applies from your next voice chat." : "")}</p>
    <button type="button" onClick={() => { setSavedNotice(false); setEditing(true); }} disabled={!profile} className="mt-2 flex min-h-11 w-full items-center justify-center gap-2 rounded-xl bg-[#3b2a22] px-4 text-sm font-semibold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#c6531d] disabled:opacity-50"><PencilSimple size={16} weight="bold" />Edit profile</button>
    {onManagePreferences && <button type="button" onClick={onManagePreferences} className="mt-3 min-h-11 w-full rounded-xl border border-[#e4cabc] px-4 text-sm font-semibold text-[#68493c] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#c6531d]">More preferences</button>}
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
      <button type="button" disabled={busy} onClick={() => requestClose("cancel")} className="rounded-full px-4 py-3 text-sm font-semibold text-[#6f5b50] transition hover:bg-[#f3e7dd] disabled:opacity-40">Cancel</button>
      <button type="submit" form={formId} disabled={busy} className="ml-auto flex flex-1 items-center justify-center gap-1.5 rounded-full bg-[#3b2a22] px-5 py-3 text-sm font-semibold text-white disabled:opacity-50 sm:flex-none">{busy && <CircleNotch size={15} className="animate-spin" />}{busy ? "Saving…" : "Save"}</button>
    </div>}
  >
    <form id={formId} onSubmit={(event) => { event.preventDefault(); void submit(); }} className="space-y-5">
      <div>
        <label htmlFor={`${baseId}-name`} className="block text-xs font-semibold text-[#5f4a3f]">What should GharSaathi call you? (optional)</label>
        <input id={`${baseId}-name`} value={values.first_name} maxLength={40} disabled={busy} onChange={(event) => update({ first_name: event.target.value })} placeholder="e.g. Moin" autoComplete="given-name" className="mt-1.5 min-h-11 w-full rounded-xl border border-[#deccbf] bg-white px-3 text-sm outline-none focus:border-[#c6531d] focus-visible:ring-2 focus-visible:ring-[#c6531d]/30" />
      </div>
      <fieldset>
        <legend className="text-xs font-semibold text-[#5f4a3f]">How should GharSaathi address you?</legend>
        <div className="mt-1.5 grid grid-cols-3 gap-2">
          {ADDRESS_FORMS.map((form) => <button key={form.id} type="button" aria-pressed={values.address_form === form.id} disabled={busy} onClick={() => update({ address_form: form.id })} className={choiceClass(values.address_form === form.id)}>{form.label}</button>)}
        </div>
        <p className="mt-2 text-[11px] text-[#8d766a]">For example: “{example}”</p>
      </fieldset>
      <fieldset>
        <legend className="text-xs font-semibold text-[#5f4a3f]">Reply language</legend>
        <div className="mt-1.5 grid grid-cols-3 gap-2">
          {LANGUAGES.map((language) => <button key={language.id} type="button" aria-pressed={values.language === language.id} disabled={busy || !languageEditable} onClick={() => update({ language: language.id })} className={choiceClass(values.language === language.id)}>{language.label}</button>)}
        </div>
      </fieldset>
      {error && <p role="alert" className="rounded-xl bg-[#fff4ef] px-3 py-2 text-xs leading-5 text-[#8f3a2a]">{error}</p>}
    </form>
  </InputDialog>;
}

function AddressesDetail({ addresses, stores, onSelectAddress, onManageAddresses, onChooseStoreAddress }: { addresses: AccountSettingsAddress[]; stores: AccountSettingsStore[]; onSelectAddress?: (addressId: string) => void; onManageAddresses?: () => void; onChooseStoreAddress?: (storeId: string) => void }) {
  const connectedStores = onChooseStoreAddress ? stores.filter((store) => store.connected) : [];
  return <div><div className="overflow-hidden rounded-2xl border border-[#ead9cd] bg-[#fffdf9]">{addresses.length ? addresses.map((address, index) => <button type="button" key={address.id} onClick={() => onSelectAddress?.(address.id)} className={`flex min-h-[72px] w-full items-center gap-3 px-3.5 text-left focus-visible:relative focus-visible:z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#c6531d] ${index ? "border-t border-[#f0e2d8]" : ""}`}><span className="grid size-10 place-items-center rounded-xl bg-[#f7e8de] text-[#bd5525]"><MapPin size={20} weight="duotone" /></span><span className="min-w-0 flex-1"><span className="block truncate text-sm font-semibold">{address.label}</span>{address.detail && <span className="mt-0.5 block truncate text-xs text-[#8d766a]">{address.detail}</span>}</span>{address.selected && <span className="grid size-6 place-items-center rounded-full bg-[#e3f0e7] text-[#347653]" aria-label="Selected address"><Check size={14} weight="bold" /></span>}</button>) : <p className="px-4 py-5 text-sm leading-6 text-[#806f65]">Add a delivery address to use with a connected store.</p>}</div>{connectedStores.map((store) => <button key={store.id} type="button" onClick={() => onChooseStoreAddress?.(store.id)} className="mt-3 flex min-h-11 w-full items-center justify-center gap-2 rounded-xl border border-[#e4cabc] bg-[#fffdf9] px-4 text-sm font-semibold text-[#68493c] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#c6531d]"><MapPin size={16} weight="bold" />Choose {store.name} address</button>)}<button type="button" onClick={onManageAddresses} disabled={!onManageAddresses} className="mt-4 min-h-11 w-full rounded-xl border border-[#e4cabc] bg-[#fffdf9] px-4 text-sm font-semibold text-[#68493c] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#c6531d] disabled:cursor-default disabled:opacity-55">Manage addresses</button></div>;
}

function initials(name: string) {
  return name.trim().split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase() || "M";
}
