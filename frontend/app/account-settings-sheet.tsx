"use client";

import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowLeft,
  CaretRight,
  Check,
  CircleNotch,
  Eye,
  MapPin,
  ShieldCheck,
  SignOut,
  Storefront,
  Translate,
  X,
} from "@phosphor-icons/react";
import Image from "next/image";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useApiRequest } from "./authenticated-api";

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
  onSignOut: () => void;
  signOutPending?: boolean;
};

type SettingsSection = "stores" | "preferences" | "privacy" | "addresses" | null;

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
        className="relative flex h-[100dvh] w-[min(75vw,360px)] flex-col overflow-hidden rounded-r-[36px] border-r border-[#ead7ca] bg-[#fffaf5] text-[#35241c] shadow-[18px_0_55px_-36px_rgba(62,34,22,.52)] [corner-shape:squircle]"
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
                {section === "privacy" && <SimpleDetail icon={<Eye size={24} weight="duotone" />} title={privacySummary} description="You choose what GharSaathi remembers. Shopping history alone never becomes a stock prediction." action="Review privacy & memory" onAction={onManagePrivacy} />}
                {section === "addresses" && <AddressesDetail addresses={addresses} onSelectAddress={onSelectAddress} onManageAddresses={onManageAddresses} />}
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

const ADDRESS_FORMS: { id: AddressForm; label: string; example: string }[] = [
  { id: "masculine", label: "Masculine", example: "aap dhoondh rahe hain" },
  { id: "feminine", label: "Feminine", example: "aap dhoondh rahi hain" },
  { id: "neutral", label: "Neutral", example: "aapko kya chahiye" },
];

function PreferencesDetail({ languageLabel, onLanguageChange, onManagePreferences }: { languageLabel: string; onLanguageChange?: (language: "hinglish" | "hindi" | "english") => void; onManagePreferences?: () => void }) {
  const request = useApiRequest();
  const languages = [
    { id: "hinglish" as const, label: "Hinglish" },
    { id: "hindi" as const, label: "Hindi" },
    { id: "english" as const, label: "English" },
  ];
  return <div className="rounded-2xl border border-[#ead9cd] bg-[#fffdf9] p-4">
    <span className="grid size-11 place-items-center rounded-xl bg-[#f7e8de] text-[#bd5525]"><Translate size={24} weight="duotone" /></span>
    <h3 className="mt-4 text-base font-semibold tracking-[-.02em]">Chat language</h3>
    <p className="mt-2 text-sm leading-6 text-[#806f65]">Choose how GharSaathi speaks and writes with you.</p>
    <div className="mt-4 grid grid-cols-3 gap-2">
      {languages.map((language) => {
        const selected = languageLabel.toLowerCase() === language.id;
        return <button key={language.id} type="button" aria-pressed={selected} onClick={() => { onLanguageChange?.(language.id); void request<VoiceProfile>("/api/profile", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ language: language.id }) }).catch(() => undefined); }} disabled={!onLanguageChange} className={`min-h-11 rounded-xl px-2 text-xs font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#c6531d] disabled:cursor-default ${selected ? "bg-[#c6531d] text-white" : "border border-[#e4cabc] text-[#68493c]"}`}>{language.label}</button>;
      })}
    </div>
    <AddressPreference />
    {onManagePreferences && <button type="button" onClick={onManagePreferences} className="mt-4 min-h-11 w-full rounded-xl border border-[#e4cabc] px-4 text-sm font-semibold text-[#68493c] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#c6531d]">More preferences</button>}
  </div>;
}

function AddressPreference() {
  const request = useApiRequest();
  const nameId = useId();
  const [profile, setProfile] = useState<VoiceProfile | null>(null);
  const [name, setName] = useState("");
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void request<VoiceProfile>("/api/profile")
      .then((loaded) => { if (active) { setProfile(loaded); setName(loaded.first_name ?? ""); } })
      .catch(() => { if (active) { setProfile({ first_name: null, address_form: "neutral", language: "hinglish" }); setStatus("error"); setError("Abhi profile load nahi ho paaya."); } });
    return () => { active = false; };
  }, [request]);

  async function save(update: Partial<VoiceProfile>) {
    setStatus("saving");
    setError("");
    try {
      const saved = await request<VoiceProfile>("/api/profile", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(update) });
      setProfile(saved);
      setName(saved.first_name ?? "");
      setStatus("saved");
    } catch (cause) {
      setStatus("error");
      setError(cause instanceof Error && cause.message !== "Request failed." ? cause.message : "Save nahi ho paaya. Naam mein sirf letters rakhiye.");
    }
  }

  if (!profile) return <div className="mt-5 h-28 animate-pulse rounded-xl bg-[#f3e6dc]" aria-label="Loading profile" />;
  const nameChanged = name.trim() !== (profile.first_name ?? "");
  return <section className="mt-5 border-t border-[#f0e2d8] pt-4" aria-label="Aapko kaise address karein?">
    <h3 className="text-base font-semibold tracking-[-.02em]">Aapko kaise address karein?</h3>
    <p className="mt-1 text-xs leading-5 text-[#806f65]">GharSaathi aapse baat karte waqt yahi use karegi. Aap kabhi bhi badal sakte hain.</p>
    <div className="mt-3 grid grid-cols-3 gap-2" role="group" aria-label="Address form">
      {ADDRESS_FORMS.map((form) => {
        const selected = profile.address_form === form.id;
        return <button key={form.id} type="button" aria-pressed={selected} disabled={status === "saving"} onClick={() => void save({ address_form: form.id })} className={`min-h-11 rounded-xl px-2 text-xs font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#c6531d] disabled:opacity-60 ${selected ? "bg-[#c6531d] text-white" : "border border-[#e4cabc] text-[#68493c]"}`}>{form.label}</button>;
      })}
    </div>
    <p className="mt-2 text-[11px] text-[#8d766a]">Jaise: “{ADDRESS_FORMS.find((form) => form.id === profile.address_form)?.example}”</p>
    <label htmlFor={nameId} className="mt-4 block text-[11px] font-semibold text-[#5f4a3f]">Aapko kis naam se bulayein? (optional)</label>
    <div className="mt-2 flex gap-2">
      <input id={nameId} value={name} maxLength={40} onChange={(event) => { setName(event.target.value); setStatus("idle"); }} placeholder="Jaise: Moin" className="min-h-11 min-w-0 flex-1 rounded-xl border border-[#deccbf] bg-white px-3 text-sm outline-none focus:border-[#c6531d]" />
      <button type="button" disabled={!nameChanged || status === "saving"} onClick={() => void save({ first_name: name.trim() || null })} className="min-h-11 rounded-xl bg-[#3b2a22] px-4 text-xs font-semibold text-white disabled:opacity-40">{status === "saving" ? "Saving…" : "Save"}</button>
    </div>
    <p aria-live="polite" className={`mt-2 min-h-4 text-[11px] ${status === "error" ? "text-[#a33d2c]" : "text-[#55745c]"}`}>{status === "saved" ? "Save ho gaya. Agli voice baat se lagu hoga." : status === "error" ? error : ""}</p>
  </section>;
}

function AddressesDetail({ addresses, onSelectAddress, onManageAddresses }: { addresses: AccountSettingsAddress[]; onSelectAddress?: (addressId: string) => void; onManageAddresses?: () => void }) {
  return <div><div className="overflow-hidden rounded-2xl border border-[#ead9cd] bg-[#fffdf9]">{addresses.length ? addresses.map((address, index) => <button type="button" key={address.id} onClick={() => onSelectAddress?.(address.id)} className={`flex min-h-[72px] w-full items-center gap-3 px-3.5 text-left focus-visible:relative focus-visible:z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#c6531d] ${index ? "border-t border-[#f0e2d8]" : ""}`}><span className="grid size-10 place-items-center rounded-xl bg-[#f7e8de] text-[#bd5525]"><MapPin size={20} weight="duotone" /></span><span className="min-w-0 flex-1"><span className="block truncate text-sm font-semibold">{address.label}</span>{address.detail && <span className="mt-0.5 block truncate text-xs text-[#8d766a]">{address.detail}</span>}</span>{address.selected && <span className="grid size-6 place-items-center rounded-full bg-[#e3f0e7] text-[#347653]" aria-label="Selected address"><Check size={14} weight="bold" /></span>}</button>) : <p className="px-4 py-5 text-sm leading-6 text-[#806f65]">Add a delivery address to use with a connected store.</p>}</div><button type="button" onClick={onManageAddresses} disabled={!onManageAddresses} className="mt-4 min-h-11 w-full rounded-xl border border-[#e4cabc] bg-[#fffdf9] px-4 text-sm font-semibold text-[#68493c] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#c6531d] disabled:cursor-default disabled:opacity-55">Manage addresses</button></div>;
}

function initials(name: string) {
  return name.trim().split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase() || "M";
}
