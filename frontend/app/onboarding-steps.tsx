"use client";

import {
  ArrowClockwise,
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle,
  CircleNotch,
  Clock,
  Eye,
  LockKey,
  MapPin,
  Microphone,
  Plus,
  ShieldCheck,
  Sparkle,
  Storefront,
  Trash,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { FormEvent, ReactNode, useEffect, useId, useRef, useState } from "react";
import {
  ADDRESS_FORM_OPTIONS,
  addressFormExample,
  FIRST_NAME_MAX_LENGTH,
  validateProfileDraft,
  type AddressForm,
  type HouseholdProfile,
  type OnboardingStep,
  type ProfileDraft,
  type ReplyLanguage,
} from "./onboarding-flow.mjs";
import { providerLabel, RETENTION_CHOICES, usePurchaseHistoryImport, type HistoryProvider, type ProviderImportProgress } from "./purchase-history-import";
import { STORE_PROVIDERS, storeName, type CommerceMode, type StoreProvider, type StoreState } from "./store-setup";
import { AddressFormSheet } from "./address-form-sheet";

const EASE_OUT = [0.23, 1, 0.32, 1] as const;
const focusRing = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-surface-muted";
const primaryButton = `flex min-h-[52px] w-full items-center justify-center gap-2 rounded-full bg-accent px-5 text-[15px] font-semibold text-on-accent shadow-[0_18px_38px_-20px_rgb(var(--accent-shadow)/.8)] transition-[transform,background-color,opacity] duration-150 ease-out hover:bg-accent-hover active:scale-[.97] disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100 ${focusRing}`;
const secondaryButton = `flex min-h-[52px] w-full items-center justify-center gap-2 rounded-full border border-accent-border bg-surface px-5 text-[15px] font-semibold text-ink-secondary transition-[transform,background-color,opacity] duration-150 ease-out hover:bg-canvas active:scale-[.97] disabled:cursor-not-allowed disabled:opacity-50 ${focusRing}`;
const textButton = `inline-flex min-h-11 items-center gap-1.5 rounded-full px-2 text-[13px] font-semibold text-accent-strong transition-opacity hover:opacity-80 disabled:cursor-not-allowed disabled:opacity-45 ${focusRing}`;

export const STEP_TITLES: Record<OnboardingStep, string> = {
  profile: "Your name",
  consent: "Terms and privacy",
  stores: "Connect stores",
  address: "Delivery address",
  personalize: "Your usual items",
  done: "All set",
};

/* ------------------------------------------------------------------ shell */

export function OnboardingHeader({ position, onBack, onSkip, onClose, stepLabel }: {
  position: { current: number; total: number; complete: boolean };
  stepLabel: string;
  onBack?: () => void;
  onSkip?: () => void;
  onClose?: () => void;
}) {
  return <header className="flex h-12 items-center gap-3">
    {onBack
      ? <button type="button" onClick={onBack} aria-label="Previous step" className={`grid size-10 shrink-0 place-items-center rounded-full border border-border-strong bg-surface text-ink-secondary transition-transform duration-150 ease-out active:scale-[.96] ${focusRing}`}><ArrowLeft size={18} weight="bold" /></button>
      : <span className="size-10 shrink-0" aria-hidden="true" />}
    <div className="min-w-0 flex-1">
      <div className="grid grid-cols-5 gap-1" aria-hidden="true">
        {Array.from({ length: position.total }, (_, index) => {
          const filled = position.complete || index < position.current;
          return <span key={index} className="h-1 overflow-hidden rounded-full bg-border-strong"><span className="block h-full origin-left rounded-full bg-accent transition-transform duration-300 ease-out" style={{ transform: `scaleX(${filled ? 1 : 0})` }} /></span>;
        })}
      </div>
      <p className="mt-1.5 text-[11px] font-semibold text-muted" aria-live="polite">{position.complete ? "Setup complete" : `Step ${position.current} / ${position.total}`}<span className="sr-only">: {stepLabel}</span></p>
    </div>
    {onClose
      ? <button type="button" onClick={onClose} aria-label="Close setup" className={`grid size-10 shrink-0 place-items-center rounded-full text-ink-secondary transition-transform duration-150 ease-out hover:bg-surface-strong active:scale-[.96] ${focusRing}`}><X size={18} weight="bold" /></button>
      : onSkip
        ? <button type="button" onClick={onSkip} className={`min-h-10 shrink-0 rounded-full px-3 text-[13px] font-semibold text-muted transition-colors hover:bg-surface-strong ${focusRing}`}>Skip for now</button>
        : <span className="size-10 shrink-0" aria-hidden="true" />}
  </header>;
}

/** Direction-aware step transition; reduced motion falls back to a plain cross-fade. */
export function StepTransition({ stepKey, direction, children }: { stepKey: string; direction: 1 | -1; children: ReactNode }) {
  const reduceMotion = useReducedMotion();
  const offset = reduceMotion ? 0 : 16;
  return <AnimatePresence mode="wait" initial={false} custom={direction}>
    <motion.div
      key={stepKey}
      custom={direction}
      variants={{
        enter: (dir: number) => ({ opacity: 0, x: dir * offset }),
        center: { opacity: 1, x: 0, transition: { duration: reduceMotion ? 0.16 : 0.24, ease: EASE_OUT } },
        exit: (dir: number) => ({ opacity: 0, x: dir * -offset * 0.75, transition: { duration: 0.12, ease: EASE_OUT } }),
      }}
      initial="enter"
      animate="center"
      exit="exit"
      className="flex min-h-0 flex-1 flex-col"
    >
      {children}
    </motion.div>
  </AnimatePresence>;
}

function StepFrame({ title, intro, children, footer, focusHeading, as = "div", onSubmit }: {
  title: ReactNode;
  intro?: ReactNode;
  children?: ReactNode;
  footer: ReactNode;
  focusHeading: boolean;
  as?: "div" | "form";
  onSubmit?: (event: FormEvent<HTMLFormElement>) => void;
}) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (focusHeading) headingRef.current?.focus({ preventScroll: true });
  }, [focusHeading]);
  const body = <>
    <div className="pt-8">
      <h1 ref={headingRef} tabIndex={-1} className="font-display text-[30px] font-semibold leading-[1.1] tracking-[-.045em] text-ink outline-none sm:text-[32px]">{title}</h1>
      {intro && <p className="mt-3 max-w-[34ch] text-[15px] leading-6 text-muted">{intro}</p>}
    </div>
    <div className="mt-7 flex-1">{children}</div>
    <div className="sticky bottom-0 -mx-5 mt-6 bg-gradient-to-t from-surface-muted via-surface-muted to-surface-muted/0 px-5 pb-[max(1.25rem,env(safe-area-inset-bottom))] pt-5">{footer}</div>
  </>;
  if (as === "form") return <form noValidate onSubmit={onSubmit} className="flex min-h-0 flex-1 flex-col">{body}</form>;
  return <div className="flex min-h-0 flex-1 flex-col">{body}</div>;
}

function InlineAlert({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return <div role="alert" className="mt-3 flex items-start gap-2 rounded-2xl bg-danger-soft px-3.5 py-3 text-[13px] leading-5 text-danger-hover">
    <WarningCircle size={17} weight="fill" className="mt-0.5 shrink-0" />
    <div className="min-w-0 flex-1"><p>{children}</p>{action}</div>
  </div>;
}

function Spinner({ size = 17 }: { size?: number }) {
  return <CircleNotch size={size} weight="bold" className="animate-spin" aria-hidden="true" />;
}

function Note({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return <div className="flex items-start gap-2.5 rounded-2xl bg-surface-strong px-3.5 py-3 text-[13px] leading-5 text-ink-secondary"><span className="mt-0.5 shrink-0 text-accent-ink">{icon}</span><span>{children}</span></div>;
}

/* ---------------------------------------------------------------- profile */

const LANGUAGE_OPTIONS: Array<{ value: ReplyLanguage; label: string; note: string }> = [
  { value: "english", label: "English", note: "Plain English" },
  { value: "hinglish", label: "Hinglish", note: "Recommended" },
  { value: "hindi", label: "Hindi", note: "हिंदी" },
];

export function ProfileStep({ initial, focusHeading, onSave }: {
  initial: ProfileDraft;
  focusHeading: boolean;
  onSave: (profile: HouseholdProfile) => Promise<void>;
}) {
  const [firstName, setFirstName] = useState(initial.firstName);
  const [addressForm, setAddressForm] = useState<AddressForm>(initial.addressForm ?? "neutral");
  const [language, setLanguage] = useState<ReplyLanguage>(initial.language);
  const [touched, setTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const nameInputRef = useRef<HTMLInputElement>(null);
  const reduceMotion = useReducedMotion();
  const ids = { name: useId(), nameError: useId(), preview: useId() };
  const validation = validateProfileDraft({ firstName, addressForm, language });
  const nameError = touched ? validation.errors.firstName : undefined;
  const example = addressFormExample(addressForm, language, firstName);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setTouched(true);
    if (!validation.ok || !validation.value) {
      nameInputRef.current?.focus();
      return;
    }
    setSaving(true);
    setSaveError("");
    try {
      await onSave(validation.value);
    } catch (cause) {
      setSaveError(cause instanceof Error ? cause.message : "Could not save your profile. Please try again.");
      setSaving(false);
    }
  }

  return <StepFrame
    as="form"
    onSubmit={submit}
    focusHeading={focusHeading}
    title="What should GharSaathi call you?"
    intro="GharSaathi will use this name when talking to you. You can change all of this later in settings."
    footer={<>
      {saveError && <InlineAlert>{saveError}</InlineAlert>}
      <button type="submit" disabled={saving} className={`${primaryButton} ${saveError ? "mt-3" : ""}`}>{saving ? <><Spinner /> Saving…</> : <>Continue <ArrowRight size={17} weight="bold" /></>}</button>
    </>}
  >
    <label htmlFor={ids.name} className="text-[13px] font-semibold text-ink-secondary">First name</label>
    <input
      ref={nameInputRef}
      id={ids.name}
      value={firstName}
      onChange={(event) => setFirstName(event.target.value)}
      onBlur={() => setTouched(true)}
      autoComplete="given-name"
      autoCapitalize="words"
      enterKeyHint="next"
      maxLength={FIRST_NAME_MAX_LENGTH + 10}
      aria-invalid={Boolean(nameError)}
      aria-describedby={nameError ? ids.nameError : undefined}
      placeholder="e.g. Anita"
      className={`mt-2 w-full rounded-2xl border bg-surface px-4 py-3.5 text-base text-ink outline-none transition-[border-color,box-shadow] duration-150 placeholder:text-subtle focus:ring-2 ${nameError ? "border-danger focus:ring-danger/20" : "border-accent-border focus:border-accent focus:ring-focus/15"}`}
    />
    <p id={ids.nameError} className="mt-2 min-h-5 text-[13px] leading-5 text-danger" aria-live="polite">{nameError ?? ""}</p>

    <fieldset className="mt-3">
      <legend className="text-[13px] font-semibold text-ink-secondary">How should GharSaathi address you?</legend>
      <div className="mt-2 grid grid-cols-3 gap-2">
        {ADDRESS_FORM_OPTIONS.map((option) => {
          const selected = addressForm === option.value;
          // A horizontal segmented choice: label on top, the example underneath.
          return <label key={option.value} className={`relative flex min-h-[52px] cursor-pointer flex-col items-center justify-center rounded-2xl border px-2 py-2 text-center transition-colors duration-150 has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-focus ${selected ? "border-accent bg-accent-soft" : "border-border-strong bg-surface hover:bg-surface-muted"}`}>
            <input type="radio" name="address-form" value={option.value} checked={selected} onChange={() => setAddressForm(option.value)} className="sr-only" />
            <span className={`block text-[14px] font-semibold ${selected ? "text-accent-ink" : "text-ink"}`}>{option.label}</span>
          </label>;
        })}
      </div>
    </fieldset>

    <div className="mt-4" aria-live="polite" id={ids.preview}>
      <p className="text-[11px] font-semibold text-subtle">How GharSaathi will reply</p>
      <div className="mt-1.5 min-h-[76px] rounded-[7px_20px_20px_20px] bg-surface px-4 py-3.5 shadow-[0_16px_40px_-34px_rgb(var(--shadow)/.7)]">
        <AnimatePresence mode="wait" initial={false}>
          <motion.p
            key={example.text}
            initial={reduceMotion ? { opacity: 0 } : { opacity: 0, filter: "blur(2px)" }}
            animate={{ opacity: 1, filter: "blur(0px)", transition: { duration: 0.18, ease: EASE_OUT } }}
            exit={{ opacity: 0, transition: { duration: 0.08 } }}
            lang={language === "hindi" ? "hi" : language === "english" ? "en" : "hi-Latn"}
            className="text-[15px] leading-6 text-ink"
          >{example.text}</motion.p>
        </AnimatePresence>
      </div>
      {!example.gendered && addressForm !== "neutral" && language === "english" && <p className="mt-1.5 text-[12px] leading-5 text-muted">English replies rarely show this, but it will be used in Hinglish and Hindi replies.</p>}
    </div>

    <fieldset className="mt-6">
      <legend className="text-[13px] font-semibold text-ink-secondary">Reply language</legend>
      <div className="mt-2 grid grid-cols-3 gap-2">
        {LANGUAGE_OPTIONS.map((option) => {
          const selected = language === option.value;
          return <label key={option.value} className={`relative flex min-h-[64px] cursor-pointer flex-col items-center justify-center rounded-2xl border px-2 text-center transition-[border-color,background-color,transform] duration-150 ease-out active:scale-[.97] has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-focus ${selected ? "border-accent bg-accent-soft ring-1 ring-focus" : "border-border-strong bg-surface"}`}>
            <input type="radio" name="reply-language" value={option.value} checked={selected} onChange={() => setLanguage(option.value)} className="sr-only" />
            <span className={`text-[14px] font-semibold ${selected ? "text-accent-strong" : "text-ink-secondary"}`}>{option.label}</span>
            <span className="mt-0.5 text-[11px] text-muted" lang={option.value === "hindi" ? "hi" : undefined}>{option.note}</span>
          </label>;
        })}
      </div>
    </fieldset>
  </StepFrame>;
}

/* ---------------------------------------------------------------- consent */

export function ConsentStep({ updatedTerms, focusHeading, onAccept }: {
  updatedTerms: boolean;
  focusHeading: boolean;
  onAccept: (personalizationAllowed: boolean) => Promise<void>;
}) {
  const [legalAccepted, setLegalAccepted] = useState(false);
  const [personalizationAllowed, setPersonalizationAllowed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!legalAccepted) {
      setError("Please accept the Terms and Privacy Policy to continue.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await onAccept(personalizationAllowed);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save your consent. Please try again.");
      setSaving(false);
    }
  }

  return <StepFrame
    as="form"
    onSubmit={submit}
    focusHeading={focusHeading}
    title={updatedTerms ? "Our Terms have been updated" : "Before you start"}
    intro={updatedTerms ? "Please review the new Terms and Privacy Policy to continue." : "Three promises, and our Terms."}
    footer={<>
      {error && <InlineAlert>{error}</InlineAlert>}
      <button type="submit" disabled={saving || !legalAccepted} className={`${primaryButton} ${error ? "mt-3" : ""}`}>{saving ? <><Spinner /> Saving…</> : <>Accept and continue <ArrowRight size={17} weight="bold" /></>}</button>
    </>}
  >
    {!updatedTerms && <ul className="mt-2 space-y-6">
      {[
        { icon: <ShieldCheck size={20} weight="duotone" />, text: "No order is placed without your approval." },
        { icon: <LockKey size={20} weight="duotone" />, text: "GharSaathi never asks for your password or OTP." },
        { icon: <Trash size={20} weight="duotone" />, text: "Anything GharSaathi remembers can be deleted at any time." },
      ].map((item) => <li key={item.text} className="flex items-center gap-4 text-[15px] leading-6 text-ink"><span className="grid size-10 shrink-0 place-items-center rounded-2xl bg-accent-soft text-accent-ink">{item.icon}</span>{item.text}</li>)}
    </ul>}

    <label className={`${updatedTerms ? "" : "mt-12"} relative flex cursor-pointer items-start gap-3 rounded-[20px] border border-border-strong bg-surface p-4 text-[14px] leading-6 text-ink-secondary has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-focus`}>
      <input type="checkbox" checked={legalAccepted} onChange={(event) => { setLegalAccepted(event.target.checked); if (event.target.checked) setError(""); }} className="peer mt-0.5 size-[22px] shrink-0 cursor-pointer appearance-none rounded-[7px] border-[1.5px] border-border-strong bg-surface transition-colors checked:border-accent checked:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/50" /><svg aria-hidden="true" viewBox="0 0 16 16" className="pointer-events-none absolute left-[20px] top-[22px] size-[14px] text-on-accent opacity-0 peer-checked:opacity-100"><path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg>
      <span>I agree to GharSaathi&apos;s <a href="/terms" target="_blank" rel="noreferrer" className="font-semibold text-accent-strong underline underline-offset-2">Terms</a> and <a href="/privacy" target="_blank" rel="noreferrer" className="font-semibold text-accent-strong underline underline-offset-2">Privacy Policy</a>.</span>
    </label>
    <label className="relative mt-4 flex cursor-pointer items-start gap-3 rounded-[20px] bg-surface-strong p-4 text-[14px] leading-6 text-ink-secondary has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-focus">
      <input type="checkbox" checked={personalizationAllowed} onChange={(event) => setPersonalizationAllowed(event.target.checked)} className="peer mt-0.5 size-[22px] shrink-0 cursor-pointer appearance-none rounded-[7px] border-[1.5px] border-border-strong bg-surface transition-colors checked:border-accent checked:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/50" /><svg aria-hidden="true" viewBox="0 0 16 16" className="pointer-events-none absolute left-[20px] top-[22px] size-[14px] text-on-accent opacity-0 peer-checked:opacity-100"><path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg>
      <span><strong className="font-semibold text-ink-secondary">Optional:</strong> Turn on Memory so GharSaathi remembers your brands, sizes and things to avoid.</span>
    </label>
    <p className="mt-4 text-[12px] leading-5 text-muted">Terms are required; Memory is optional and can be changed anytime in Settings. Order history is never imported unless you ask.</p>
  </StepFrame>;
}

/* ----------------------------------------------------------------- stores */

const STORE_ACCESS = ["Products, prices and delivery time", "Your saved delivery addresses", "Baskets and orders, only with your approval"];

export function StoresStep({ stores, commerceMode, focusHeading, oauthReturned, onConnect, onRetryStatus, onContinue, onSkip }: {
  stores: Record<StoreProvider, StoreState>;
  commerceMode: CommerceMode | null;
  focusHeading: boolean;
  oauthReturned: StoreProvider | null;
  onConnect: (provider: StoreProvider) => void;
  onRetryStatus: (provider: StoreProvider) => void;
  onContinue: () => void;
  onSkip: () => void;
}) {
  const demo = commerceMode === "demo";
  const anyConnected = STORE_PROVIDERS.some((provider) => stores[provider].connection === "connected");
  const checking = STORE_PROVIDERS.some((provider) => stores[provider].connection === "checking");
  return <StepFrame
    focusHeading={focusHeading}
    title="Connect your stores"
    intro={demo ? "In demo mode, both stores are ready with sample data." : "GharSaathi searches and compares prices at every store you connect. One store is enough."}
    footer={anyConnected
      ? <button type="button" onClick={onContinue} className={primaryButton}>Continue <ArrowRight size={17} weight="bold" /></button>
      : <button type="button" onClick={onSkip} disabled={checking} className={secondaryButton}>Not now</button>}
  >
    {oauthReturned && stores[oauthReturned].connection === "connected" && <div role="status" className="mb-4 flex items-center gap-2 rounded-2xl bg-success-soft px-3.5 py-3 text-[13px] font-semibold text-success-strong"><CheckCircle size={18} weight="fill" /> {storeName(oauthReturned)} connected.</div>}
    <div className="space-y-3">
      {STORE_PROVIDERS.map((provider) => <StoreCard key={provider} provider={provider} state={stores[provider]} demo={demo} onConnect={() => onConnect(provider)} onRetryStatus={() => onRetryStatus(provider)} />)}
    </div>
    {!demo && <div className="mt-4"><Note icon={<LockKey size={17} weight="duotone" />}>You sign in on the store&apos;s own page and come straight back here. GharSaathi never sees or asks for your password or OTP.</Note></div>}
  </StepFrame>;
}

function StoreCard({ provider, state, demo, onConnect, onRetryStatus }: { provider: StoreProvider; state: StoreState; demo: boolean; onConnect: () => void; onRetryStatus: () => void }) {
  const name = storeName(provider);
  const connected = state.connection === "connected";
  const status = state.connection === "checking" ? "Checking…" : demo ? "Demo ready" : connected ? "Connected" : state.connection === "error" ? "Status unavailable" : "Not connected";
  return <section aria-label={name} className={`rounded-[22px] border p-4 transition-colors duration-200 ${connected ? "border-success-border bg-success-soft" : "border-border bg-surface"}`}>
    <div className="flex items-center gap-3">
      <span className={`grid size-10 shrink-0 place-items-center rounded-xl ${connected ? "bg-success-border text-success" : "bg-accent-soft text-accent-hover"}`}>{connected ? <Check size={20} weight="bold" /> : <Storefront size={21} weight="duotone" />}</span>
      <div className="min-w-0 flex-1">
        <h2 className="text-[16px] font-semibold text-ink">{name}</h2>
        <p className={`text-[12px] font-semibold ${connected ? "text-success" : "text-muted"}`}>{state.connection === "checking" ? <span className="inline-flex items-center gap-1.5"><Spinner size={12} /> {status}</span> : status}</p>
      </div>
    </div>
    {!connected && state.connection !== "checking" && <>
      <ul className="mt-3 space-y-1.5 text-[13px] leading-5 text-ink-secondary" aria-label={`What ${name} shares`}>
        {STORE_ACCESS.map((item) => <li key={item} className="flex items-start gap-2"><Eye size={15} className="mt-0.5 shrink-0 text-subtle" aria-hidden="true" />{item}</li>)}
      </ul>
      {state.connection === "error"
        ? <InlineAlert action={<button type="button" onClick={onRetryStatus} className={`${textButton} -ml-2 mt-1`}><ArrowClockwise size={14} weight="bold" /> Check again</button>}>{state.connectionError}</InlineAlert>
        : <button type="button" onClick={onConnect} disabled={state.connecting} className={`${provider === "zepto" ? primaryButton : secondaryButton} mt-4`}>{state.connecting ? <><Spinner /> Opening login page…</> : <>Sign in with {name} <ArrowRight size={17} weight="bold" /></>}</button>}
      {state.connectError && <InlineAlert>{state.connectError}</InlineAlert>}
    </>}
  </section>;
}

/* ---------------------------------------------------------------- address */

export function AddressStep({ stores, commerceMode, focusHeading, onLoad, onSelect, onAddressesAdded, onContinue, onSkip }: {
  stores: Record<StoreProvider, StoreState>;
  commerceMode: CommerceMode | null;
  focusHeading: boolean;
  onLoad: (provider: StoreProvider, refresh?: boolean) => void;
  onSelect: (provider: StoreProvider, addressId: string) => void;
  onAddressesAdded: (provider: StoreProvider, data: { addresses: StoreState["addresses"]; resolution: { default_address_id?: string | null } }) => void;
  onContinue: () => void;
  onSkip: () => void;
}) {
  const connected = STORE_PROVIDERS.filter((provider) => stores[provider].connection === "connected");
  const ready = connected.some((provider) => stores[provider].selectedAddressId);
  const busy = connected.some((provider) => stores[provider].selecting);
  const missing = connected.filter((provider) => !stores[provider].selectedAddressId);

  useEffect(() => {
    for (const provider of connected) if (stores[provider].addressState === "idle") onLoad(provider);
  }, [connected, onLoad, stores]);

  return <StepFrame
    focusHeading={focusHeading}
    title="Where should we deliver?"
    intro={commerceMode === "demo" ? "Demo mode uses a sample address. Your real addresses appear in live mode." : "Choose a saved address for each store. Prices and delivery times are checked for this address."}
    footer={<>
      {ready && missing.length > 0 && <p className="mb-3 text-center text-[12px] leading-5 text-muted">You can choose a {missing.map(storeName).join(" and ")} address later.</p>}
      {ready
        ? <button type="button" onClick={onContinue} disabled={busy} className={primaryButton}>{busy ? <><Spinner /> Saving…</> : <>Use this address <ArrowRight size={17} weight="bold" /></>}</button>
        : <button type="button" onClick={onSkip} className={secondaryButton}>Skip for now</button>}
    </>}
  >
    <div className="space-y-6">
      {connected.map((provider) => <StoreAddresses key={provider} provider={provider} state={stores[provider]} demo={commerceMode === "demo"} onLoad={(refresh) => onLoad(provider, refresh)} onSelect={(id) => onSelect(provider, id)} onAdded={(data) => onAddressesAdded(provider, data)} />)}
    </div>
  </StepFrame>;
}

function StoreAddresses({ provider, state, demo, onLoad, onSelect, onAdded }: {
  provider: StoreProvider;
  state: StoreState;
  demo: boolean;
  onLoad: (refresh?: boolean) => void;
  onSelect: (addressId: string) => void;
  onAdded: (data: { addresses: StoreState["addresses"]; resolution: { default_address_id?: string | null } }) => void;
}) {
  const [formOpen, setFormOpen] = useState(false);
  const name = storeName(provider);
  const loading = state.addressState === "loading" || state.addressState === "idle";
  return <fieldset>
    <div className="flex items-center gap-2">
      <legend className="text-[15px] font-semibold text-ink">{name}</legend>
      {state.addressState === "ready" && state.addresses.length > 0 && !demo && <button type="button" onClick={() => onLoad(true)} className={`${textButton} ml-auto`}><ArrowClockwise size={14} weight="bold" /> Refresh</button>}
    </div>

    {loading && <div className="mt-2 space-y-2" aria-label={`Loading ${name} addresses`}>{[0, 1].map((item) => <div key={item} className="h-[68px] animate-pulse rounded-[20px] bg-surface-strong" />)}</div>}

    {state.addressState === "needs_refresh" && <div className="mt-2 rounded-[20px] border border-border bg-surface p-4">
      <p className="text-[14px] leading-5 text-ink-secondary">Your {name} saved addresses aren&apos;t here yet. Fetch them once and they&apos;ll stay saved here.</p>
      <button type="button" onClick={() => onLoad(true)} className={`${secondaryButton} mt-3`}><MapPin size={17} weight="bold" /> Fetch addresses from {name}</button>
    </div>}

    {state.addressState === "error" && <InlineAlert action={<button type="button" onClick={() => onLoad(false)} className={`${textButton} -ml-2 mt-1`}><ArrowClockwise size={14} weight="bold" /> Try again</button>}>{state.addressError}</InlineAlert>}

    {state.addressState === "ready" && state.addresses.length > 0 && <div className="mt-2 overflow-hidden rounded-[20px] border border-border-strong bg-surface">
      {state.addresses.map((address, index) => {
        const selected = state.selectedAddressId === address.id;
        return <label key={address.id} className={`flex min-h-[68px] cursor-pointer items-start gap-3 px-4 py-3.5 transition-colors duration-150 has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-inset has-[:focus-visible]:ring-focus ${index ? "border-t border-border" : ""} ${selected ? "bg-accent-soft" : "hover:bg-surface"}`}>
          <input type="radio" name={`${provider}-address`} value={address.id} checked={selected} disabled={state.selecting} onChange={() => onSelect(address.id)} className="sr-only" />
          <span aria-hidden="true" className={`mt-0.5 grid size-5 shrink-0 place-items-center rounded-full border-2 transition-colors duration-150 ${selected ? "border-accent bg-accent" : "border-accent-border bg-white"}`}>{selected && <span className="size-1.5 rounded-full bg-white" />}</span>
          <span className="min-w-0 flex-1"><span className="block text-[14px] font-semibold text-ink">{address.tag || address.category || "Saved address"}</span><span className="mt-0.5 block text-[13px] leading-5 text-muted">{address.address_line}</span></span>
          {selected && state.selecting && <span className="mt-0.5 text-accent-ink"><Spinner size={15} /></span>}
        </label>;
      })}
    </div>}
    {state.addressState === "ready" && state.addresses.length > 0 && state.addressError && <InlineAlert>{state.addressError}</InlineAlert>}

    {state.addressState === "ready" && state.addresses.length === 0 && <div className="mt-2 rounded-[20px] border border-border bg-surface p-4">
      <p className="text-[14px] leading-5 text-ink-secondary">{provider === "zepto" ? "No saved Zepto address found." : "No saved Swiggy address found. Add one in the Swiggy app, then tap Refresh here."}</p>
      {provider === "swiggy" && <button type="button" onClick={() => onLoad(true)} className={`${textButton} -ml-2 mt-2`}><ArrowClockwise size={14} weight="bold" /> Refresh</button>}
    </div>}

    {provider === "zepto" && !demo && state.addressState === "ready" && <button type="button" onClick={() => setFormOpen(true)} className={`${textButton} -ml-2 mt-2`}><Plus size={14} weight="bold" /> Add a new Zepto address</button>}
    {provider === "zepto" && !demo && <AddressFormSheet open={formOpen} prefillRequest={null} onAdded={(data) => { onAdded(data); setFormOpen(false); }} onClose={() => setFormOpen(false)} />}
  </fieldset>;
}

/* ------------------------------------------------------------ personalize */

export function PersonalizeStep({ swiggyConnected, zeptoConnected, commerceMode, focusHeading, onContinue }: {
  swiggyConnected: boolean;
  zeptoConnected: boolean;
  commerceMode: CommerceMode | null;
  focusHeading: boolean;
  onContinue: (decision: "done" | "skipped") => void;
}) {
  const history = usePurchaseHistoryImport({ swiggyConnected, zeptoConnected });
  const demo = commerceMode === "demo";
  const ids = { retention: useId() };
  const progressEntries = Object.entries(history.progress) as Array<[HistoryProvider, ProviderImportProgress]>;
  const importing = history.busy === "enable" || history.busy === "refresh";
  const finished = !importing && progressEntries.length > 0 && progressEntries.every(([, item]) => item.state === "done" || item.state === "failed");
  const alreadyOn = history.permission?.allowed === true && !progressEntries.length;
  const anySucceeded = progressEntries.some(([, item]) => item.state === "done");

  async function skip() {
    // "Off" is already the default, so a failed save must not trap the user here.
    await history.skip();
    onContinue("skipped");
  }

  let footer: ReactNode;
  if (history.permission === undefined) footer = <button type="button" disabled className={primaryButton}><Spinner /> Loading…</button>;
  else if (demo) footer = <button type="button" onClick={() => onContinue("skipped")} className={primaryButton}>Continue <ArrowRight size={17} weight="bold" /></button>;
  else if (importing) footer = <button type="button" disabled className={primaryButton}><Spinner /> Reading past orders…</button>;
  else if (finished || alreadyOn) footer = <button type="button" onClick={() => onContinue(anySucceeded || alreadyOn ? "done" : "skipped")} className={primaryButton}>Continue <ArrowRight size={17} weight="bold" /></button>;
  else footer = <div className="grid gap-2.5">
    <button type="button" onClick={() => void history.enable()} disabled={history.busy !== null} className={primaryButton}>Import past orders</button>
    <button type="button" onClick={() => void skip()} disabled={history.busy !== null} className={secondaryButton}>{history.busy === "skip" ? <><Spinner /> Saving…</> : "Not now"}</button>
  </div>;

  return <StepFrame
    focusHeading={focusHeading}
    title={<>Should GharSaathi remember your <span className="whitespace-nowrap">&lsquo;usual&rsquo; items?</span></>}
    intro="From your past orders, GharSaathi can ask: “The same Amul milk as last time?”"
    footer={footer}
  >
    {history.permission === undefined
      ? <div className="space-y-3" aria-label="Loading">{[0, 1, 2].map((item) => <div key={item} className="h-14 animate-pulse rounded-2xl bg-surface-strong" />)}</div>
      : <>
        <ul className="space-y-5">
          {[
            { icon: <Eye size={20} weight="duotone" />, title: "Read-only", text: "Recent orders from connected stores, read-only. Nothing is added or ordered." },
            { icon: <Sparkle size={20} weight="duotone" />, title: "“The usual?” suggestions", text: "Repeat items are spotted and suggested. GharSaathi never guesses what has run out at home." },
            { icon: <Trash size={20} weight="duotone" />, title: "Delete any time", text: "Turning this off in settings removes the imported data." },
          ].map((item) => <li key={item.title} className="flex items-start gap-4"><span className="grid size-10 shrink-0 place-items-center rounded-2xl bg-accent-soft text-accent-ink">{item.icon}</span><span><span className="block text-[14px] font-semibold text-ink">{item.title}</span><span className="mt-0.5 block text-[13px] leading-5 text-muted">{item.text}</span></span></li>)}
        </ul>

        {demo && <div className="mt-5"><Note icon={<ShieldCheck size={17} weight="duotone" />}>Import is off in demo mode. No real order history is read.</Note></div>}

        {!demo && alreadyOn && <div role="status" className="mt-5 flex items-start gap-2.5 rounded-2xl bg-success-soft px-3.5 py-3 text-[13px] leading-5 text-success-strong"><CheckCircle size={18} weight="fill" className="mt-0.5 shrink-0" /> Usuals are already on. You can turn them off in settings at any time.</div>}

        {!demo && !alreadyOn && !progressEntries.length && <fieldset className="mt-5" aria-describedby={ids.retention}>
          <legend className="text-[13px] font-semibold text-ink-secondary">Keep order history for</legend>
          <div className="mt-2 grid grid-cols-3 gap-2">
            {RETENTION_CHOICES.map((choice) => {
              const selected = history.retentionDays === choice.days;
              return <label key={choice.days} className={`flex min-h-12 cursor-pointer items-center justify-center rounded-2xl border text-[14px] font-semibold transition-[border-color,background-color,transform] duration-150 ease-out active:scale-[.97] has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-focus ${selected ? "border-accent bg-accent-soft text-accent-strong ring-1 ring-focus" : "border-border-strong bg-surface text-ink-secondary"}`}>
                <input type="radio" name="history-retention" value={choice.days} checked={selected} onChange={() => history.setRetentionDays(choice.days)} className="sr-only" />{choice.label}
              </label>;
            })}
          </div>
          <p id={ids.retention} className="mt-2 text-[12px] leading-5 text-muted">After this, imported pattern data is deleted automatically.</p>
        </fieldset>}

        {progressEntries.length > 0 && <ul className="mt-5 overflow-hidden rounded-[20px] border border-border-strong bg-surface" aria-label="Import progress" aria-live="polite">
          {progressEntries.map(([provider, item], index) => <li key={provider} className={`flex min-h-[60px] items-center gap-3 px-4 py-3 ${index ? "border-t border-border" : ""}`}>
            <span className={`grid size-8 shrink-0 place-items-center rounded-full ${item.state === "done" ? "bg-success-border text-success" : item.state === "failed" ? "bg-danger-soft text-danger" : "bg-accent-soft text-accent-hover"}`}>
              {item.state === "done" ? <Check size={16} weight="bold" /> : item.state === "failed" ? <WarningCircle size={16} weight="fill" /> : item.state === "running" ? <Spinner size={15} /> : <Clock size={16} />}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[14px] font-semibold text-ink">{providerLabel(provider)}</span>
              <span className="block text-[12px] leading-5 text-muted">{item.state === "waiting" ? "Waiting" : item.state === "running" ? "Reading…" : item.state === "failed" ? item.message : item.result.cached ? "Checked recently, not fetched again" : `${item.result.imported_orders} orders · ${item.result.imported_occurrences} items`}</span>
            </span>
          </li>)}
        </ul>}
        {finished && !anySucceeded && <p className="mt-3 text-[13px] leading-5 text-muted">Import didn&apos;t work this time. You can try again from settings after setup.</p>}
        {history.error && !progressEntries.length && <InlineAlert action={history.loadError ? <button type="button" onClick={history.reload} className={`${textButton} -ml-2 mt-1`}><ArrowClockwise size={14} weight="bold" /> Try again</button> : undefined}>{history.error}</InlineAlert>}
      </>}
  </StepFrame>;
}

/* ------------------------------------------------------------------- done */

const LANGUAGE_NAMES: Record<ReplyLanguage, string> = { hinglish: "Hinglish", english: "English", hindi: "Hindi" };

export function DoneStep({ profile, connectedStores, commerceMode, focusHeading, revisit, onFinish }: {
  profile: HouseholdProfile | null;
  connectedStores: StoreProvider[];
  commerceMode: CommerceMode | null;
  focusHeading: boolean;
  revisit: boolean;
  onFinish: () => Promise<void>;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const reduceMotion = useReducedMotion();
  const name = profile?.first_name;

  async function finish() {
    setSaving(true);
    setError("");
    try {
      await onFinish();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save setup. Please try again.");
      setSaving(false);
    }
  }

  return <StepFrame
    focusHeading={focusHeading}
    title={<>You&apos;re all set{name ? `, ${name}` : ""}.</>}
    intro={connectedStores.length ? "Just ask. GharSaathi handles the rest and checks with you before any order." : "Shopping starts once you connect a store. Until then, try the chat."}
    footer={<>
      {error && <InlineAlert>{error}</InlineAlert>}
      <button type="button" onClick={() => void finish()} disabled={saving} className={`${primaryButton} ${error ? "mt-3" : ""}`}>{saving ? <><Spinner /> One moment…</> : <>{revisit ? "Back to shopping" : "Start shopping"} <ArrowRight size={17} weight="bold" /></>}</button>
    </>}
  >
    <div className="rounded-[24px] bg-inverse p-5 text-on-inverse shadow-[0_24px_50px_-30px_rgb(var(--shadow)/.9)]">
      <div className="flex items-center gap-4">
        <motion.span
          initial={reduceMotion ? false : { scale: 0.9, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ type: "spring", duration: 0.45, bounce: 0.2, delay: 0.1 }}
          className="relative grid size-14 shrink-0 place-items-center rounded-full bg-accent text-on-accent shadow-[0_12px_30px_-14px_rgb(var(--accent-shadow)/.9)]"
          aria-hidden="true"
        ><Microphone size={24} weight="fill" /></motion.span>
        <p className="text-[14px] leading-5 text-border-strong">Tap the mic and say:</p>
      </div>
      <p className="font-display mt-4 text-[24px] font-semibold leading-tight tracking-[-.03em]">&ldquo;{profile?.language === "english" ? "I need milk and atta" : "Doodh aur atta chahiye"}&rdquo;</p>
    </div>

    <dl className="mt-5 divide-y divide-border border-y border-border text-[14px]">
      <div className="flex items-center justify-between gap-3 py-3"><dt className="text-muted">Stores</dt><dd className="text-right font-semibold text-ink">{commerceMode === "demo" ? "Demo (Zepto + Swiggy)" : connectedStores.length ? connectedStores.map(storeName).join(", ") : "None yet"}</dd></div>
      {profile && <div className="flex items-center justify-between gap-3 py-3"><dt className="text-muted">Reply language</dt><dd className="font-semibold text-ink">{LANGUAGE_NAMES[profile.language]}</dd></div>}
      <div className="flex items-center justify-between gap-3 py-3"><dt className="text-muted">Order</dt><dd className="font-semibold text-ink">Only with your approval</dd></div>
    </dl>
  </StepFrame>;
}
