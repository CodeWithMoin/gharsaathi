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
  byAddressForm,
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
import { ZeptoAddressForm } from "./zepto-address-form";

const EASE_OUT = [0.23, 1, 0.32, 1] as const;
const focusRing = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#c6531d] focus-visible:ring-offset-2 focus-visible:ring-offset-[#f8f2ea]";
const primaryButton = `flex min-h-[52px] w-full items-center justify-center gap-2 rounded-full bg-[#c6531d] px-5 text-[15px] font-semibold text-white shadow-[0_18px_38px_-20px_rgba(158,65,24,.8)] transition-[transform,background-color,opacity] duration-150 ease-out hover:bg-[#b94a18] active:scale-[.97] disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100 ${focusRing}`;
const secondaryButton = `flex min-h-[52px] w-full items-center justify-center gap-2 rounded-full border border-[#ddc8ba] bg-[#fffdf9] px-5 text-[15px] font-semibold text-[#5c463b] transition-[transform,background-color,opacity] duration-150 ease-out hover:bg-[#fff7f1] active:scale-[.97] disabled:cursor-not-allowed disabled:opacity-50 ${focusRing}`;
const textButton = `inline-flex min-h-11 items-center gap-1.5 rounded-full px-2 text-[13px] font-semibold text-[#9d421a] transition-opacity hover:opacity-80 disabled:cursor-not-allowed disabled:opacity-45 ${focusRing}`;

export const STEP_TITLES: Record<OnboardingStep, string> = {
  profile: "Aapka naam",
  consent: "Terms aur privacy",
  stores: "Stores jodiye",
  address: "Delivery address",
  personalize: "Aapki usual cheezein",
  done: "Sab set",
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
      ? <button type="button" onClick={onBack} aria-label="Pichhla step" className={`grid size-10 shrink-0 place-items-center rounded-full border border-[#e1d0c4] bg-[#fffdf9] text-[#70584b] transition-transform duration-150 ease-out active:scale-[.96] ${focusRing}`}><ArrowLeft size={18} weight="bold" /></button>
      : <span className="size-10 shrink-0" aria-hidden="true" />}
    <div className="min-w-0 flex-1">
      <div className="grid grid-cols-5 gap-1" aria-hidden="true">
        {Array.from({ length: position.total }, (_, index) => {
          const filled = position.complete || index < position.current;
          return <span key={index} className="h-1 overflow-hidden rounded-full bg-[#e6d6ca]"><span className="block h-full origin-left rounded-full bg-[#c6531d] transition-transform duration-300 ease-out" style={{ transform: `scaleX(${filled ? 1 : 0})` }} /></span>;
        })}
      </div>
      <p className="mt-1.5 text-[11px] font-semibold text-[#8e796d]" aria-live="polite">{position.complete ? "Setup poora" : `Step ${position.current} / ${position.total}`}<span className="sr-only">: {stepLabel}</span></p>
    </div>
    {onClose
      ? <button type="button" onClick={onClose} aria-label="Setup band karein" className={`grid size-10 shrink-0 place-items-center rounded-full text-[#70584b] transition-transform duration-150 ease-out hover:bg-[#f1e6dd] active:scale-[.96] ${focusRing}`}><X size={18} weight="bold" /></button>
      : onSkip
        ? <button type="button" onClick={onSkip} className={`min-h-10 shrink-0 rounded-full px-3 text-[13px] font-semibold text-[#806f65] transition-colors hover:bg-[#f1e6dd] ${focusRing}`}>Baad mein</button>
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
      <h1 ref={headingRef} tabIndex={-1} className="font-display text-[30px] font-semibold leading-[1.1] tracking-[-.045em] text-[#35241c] outline-none sm:text-[32px]">{title}</h1>
      {intro && <p className="mt-3 max-w-[34ch] text-[15px] leading-6 text-[#806f65]">{intro}</p>}
    </div>
    <div className="mt-7 flex-1">{children}</div>
    <div className="sticky bottom-0 -mx-5 mt-6 bg-gradient-to-t from-[#f8f2ea] via-[#f8f2ea] to-[#f8f2ea]/0 px-5 pb-[max(1.25rem,env(safe-area-inset-bottom))] pt-5">{footer}</div>
  </>;
  if (as === "form") return <form noValidate onSubmit={onSubmit} className="flex min-h-0 flex-1 flex-col">{body}</form>;
  return <div className="flex min-h-0 flex-1 flex-col">{body}</div>;
}

function InlineAlert({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return <div role="alert" className="mt-3 flex items-start gap-2 rounded-2xl bg-[#fcedea] px-3.5 py-3 text-[13px] leading-5 text-[#8f3a2e]">
    <WarningCircle size={17} weight="fill" className="mt-0.5 shrink-0" />
    <div className="min-w-0 flex-1"><p>{children}</p>{action}</div>
  </div>;
}

function Spinner({ size = 17 }: { size?: number }) {
  return <CircleNotch size={size} weight="bold" className="animate-spin" aria-hidden="true" />;
}

function Note({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return <div className="flex items-start gap-2.5 rounded-2xl bg-[#f1e8e0] px-3.5 py-3 text-[13px] leading-5 text-[#70584b]"><span className="mt-0.5 shrink-0 text-[#c6531d]">{icon}</span><span>{children}</span></div>;
}

/* ---------------------------------------------------------------- profile */

const LANGUAGE_OPTIONS: Array<{ value: ReplyLanguage; label: string; note: string }> = [
  { value: "hinglish", label: "Hinglish", note: "Default" },
  { value: "english", label: "English", note: "English" },
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
      setSaveError(cause instanceof Error ? cause.message : "Profile save nahi hua. Ek baar phir try kijiye.");
      setSaving(false);
    }
  }

  return <StepFrame
    as="form"
    onSubmit={submit}
    focusHeading={focusHeading}
    title="Aapko kya bulaun?"
    intro="Main isi naam se baat karungi. Yeh sab baad mein settings mein badla ja sakta hai."
    footer={<>
      {saveError && <InlineAlert>{saveError}</InlineAlert>}
      <button type="submit" disabled={saving} className={`${primaryButton} ${saveError ? "mt-3" : ""}`}>{saving ? <><Spinner /> Save ho raha hai…</> : <>Aage badhein <ArrowRight size={17} weight="bold" /></>}</button>
    </>}
  >
    <label htmlFor={ids.name} className="text-[13px] font-semibold text-[#5f4a3f]">Pehla naam</label>
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
      placeholder="Jaise: Anita"
      className={`mt-2 w-full rounded-2xl border bg-[#fffdf9] px-4 py-3.5 text-base text-[#35241c] outline-none transition-[border-color,box-shadow] duration-150 placeholder:text-[#9a877c] focus:ring-2 ${nameError ? "border-[#b4493a] focus:ring-[#b4493a]/20" : "border-[#ddc8ba] focus:border-[#c6531d] focus:ring-[#c6531d]/15"}`}
    />
    <p id={ids.nameError} className="mt-2 min-h-5 text-[13px] leading-5 text-[#a33f31]" aria-live="polite">{nameError ?? ""}</p>

    <fieldset className="mt-3">
      <legend className="text-[13px] font-semibold text-[#5f4a3f]">Aapko kaise address karun?</legend>
      <div className="mt-2 overflow-hidden rounded-[20px] border border-[#e1d0c4] bg-[#fffdf9]">
        {ADDRESS_FORM_OPTIONS.map((option, index) => {
          const selected = addressForm === option.value;
          return <label key={option.value} className={`relative flex min-h-[56px] cursor-pointer items-center gap-3 px-4 transition-colors duration-150 has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-inset has-[:focus-visible]:ring-[#c6531d] ${index ? "border-t border-[#f0e2d8]" : ""} ${selected ? "bg-[#fff4ec]" : "hover:bg-[#fffaf6]"}`}>
            <input type="radio" name="address-form" value={option.value} checked={selected} onChange={() => setAddressForm(option.value)} className="sr-only" />
            <span aria-hidden="true" className={`grid size-5 shrink-0 place-items-center rounded-full border-2 transition-colors duration-150 ${selected ? "border-[#c6531d] bg-[#c6531d]" : "border-[#cdb6a7] bg-white"}`}>{selected && <span className="size-1.5 rounded-full bg-white" />}</span>
            <span className="min-w-0 flex-1"><span className="block text-[15px] font-semibold text-[#3d2b23]">{option.label}</span><span className="block text-[13px] text-[#806f65]">&ldquo;{option.example}&rdquo;</span></span>
          </label>;
        })}
      </div>
    </fieldset>

    <div className="mt-4" aria-live="polite" id={ids.preview}>
      <p className="text-[11px] font-semibold text-[#9b8172]">Main aise bolungi</p>
      <div className="mt-1.5 min-h-[76px] rounded-[7px_20px_20px_20px] bg-[#fffdf9] px-4 py-3.5 shadow-[0_16px_40px_-34px_rgba(74,43,27,.7)]">
        <AnimatePresence mode="wait" initial={false}>
          <motion.p
            key={example.text}
            initial={reduceMotion ? { opacity: 0 } : { opacity: 0, filter: "blur(2px)" }}
            animate={{ opacity: 1, filter: "blur(0px)", transition: { duration: 0.18, ease: EASE_OUT } }}
            exit={{ opacity: 0, transition: { duration: 0.08 } }}
            lang={language === "hindi" ? "hi" : language === "english" ? "en" : "hi-Latn"}
            className="text-[15px] leading-6 text-[#35241c]"
          >{example.text}</motion.p>
        </AnimatePresence>
      </div>
      {!example.gendered && addressForm !== "neutral" && language === "english" && <p className="mt-1.5 text-[12px] leading-5 text-[#8e796d]">English mein yeh farq kam dikhta hai, par Hinglish replies mein yaad rahega.</p>}
    </div>

    <fieldset className="mt-6">
      <legend className="text-[13px] font-semibold text-[#5f4a3f]">Reply kis language mein?</legend>
      <div className="mt-2 grid grid-cols-3 gap-2">
        {LANGUAGE_OPTIONS.map((option) => {
          const selected = language === option.value;
          return <label key={option.value} className={`relative flex min-h-[64px] cursor-pointer flex-col justify-center rounded-2xl border px-3 transition-[border-color,background-color,transform] duration-150 ease-out active:scale-[.97] has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-[#c6531d] ${selected ? "border-[#c6531d] bg-[#fff4ec] ring-1 ring-[#c6531d]" : "border-[#e1d0c4] bg-[#fffdf9]"}`}>
            <input type="radio" name="reply-language" value={option.value} checked={selected} onChange={() => setLanguage(option.value)} className="sr-only" />
            <span className={`text-[14px] font-semibold ${selected ? "text-[#8f421b]" : "text-[#5c463b]"}`}>{option.label}</span>
            <span className="mt-0.5 text-[11px] text-[#8e796d]" lang={option.value === "hindi" ? "hi" : undefined}>{option.note}</span>
            {selected && <Check size={14} weight="bold" className="absolute right-2.5 top-2.5 text-[#c6531d]" aria-hidden="true" />}
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
      setError("Terms aur Privacy Policy accept karna zaroori hai.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await onAccept(personalizationAllowed);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Consent save nahi hua. Ek baar phir try kijiye.");
      setSaving(false);
    }
  }

  return <StepFrame
    as="form"
    onSubmit={submit}
    focusHeading={focusHeading}
    title={updatedTerms ? "Terms update hue hain" : "Ek zaroori baat"}
    intro={updatedTerms ? "Aage badhne ke liye naye Terms aur Privacy Policy dekh lijiye." : "Shuru karne se pehle, yeh teen vaade aur hamare Terms."}
    footer={<>
      {error && <InlineAlert>{error}</InlineAlert>}
      <button type="submit" disabled={saving || !legalAccepted} className={`${primaryButton} ${error ? "mt-3" : ""}`}>{saving ? <><Spinner /> Save ho raha hai…</> : <>Accept karke aage badhein <ArrowRight size={17} weight="bold" /></>}</button>
    </>}
  >
    {!updatedTerms && <ul className="divide-y divide-[#eaded5] border-y border-[#eaded5]">
      {[
        { icon: <ShieldCheck size={20} weight="duotone" />, text: "Aapki approval ke bina koi order nahi hoga." },
        { icon: <LockKey size={20} weight="duotone" />, text: "Password ya OTP main kabhi nahi maangungi." },
        { icon: <Trash size={20} weight="duotone" />, text: "Jo yaad rakhun, woh kabhi bhi delete kiya ja sakta hai." },
      ].map((item) => <li key={item.text} className="flex items-start gap-3 py-3.5 text-[14px] leading-5 text-[#5f4a3f]"><span className="mt-0.5 shrink-0 text-[#c6531d]">{item.icon}</span>{item.text}</li>)}
    </ul>}

    <label className={`${updatedTerms ? "" : "mt-6"} flex cursor-pointer items-start gap-3 rounded-[20px] border border-[#e1d0c4] bg-[#fffdf9] p-4 text-[14px] leading-6 text-[#5f4a3f] has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-[#c6531d]`}>
      <input type="checkbox" checked={legalAccepted} onChange={(event) => { setLegalAccepted(event.target.checked); if (event.target.checked) setError(""); }} className="mt-1 size-[18px] shrink-0 accent-[#c6531d]" />
      <span>I agree to GharSaathi&apos;s <a href="/terms" target="_blank" rel="noreferrer" className="font-semibold text-[#9d421a] underline underline-offset-2">Terms</a> and <a href="/privacy" target="_blank" rel="noreferrer" className="font-semibold text-[#9d421a] underline underline-offset-2">Privacy Policy</a>.</span>
    </label>
    <label className="mt-3 flex cursor-pointer items-start gap-3 rounded-[20px] bg-[#f1e8e0] p-4 text-[14px] leading-6 text-[#70584b] has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-[#c6531d]">
      <input type="checkbox" checked={personalizationAllowed} onChange={(event) => setPersonalizationAllowed(event.target.checked)} className="mt-1 size-[18px] shrink-0 accent-[#c6531d]" />
      <span><strong className="font-semibold text-[#5f4032]">Optional:</strong> Allow personalisation from saved preferences. Shopping-history import stays a separate choice and is never enabled automatically.</span>
    </label>
    <p className="mt-3 text-[12px] leading-5 text-[#806f65]">You can change personalisation choices later. Accepting Terms is required; personalisation is not.</p>
  </StepFrame>;
}

/* ----------------------------------------------------------------- stores */

const STORE_ACCESS = ["Products, prices aur delivery time", "Aapke saved delivery addresses", "Basket aur orders, sirf aapki approval par"];

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
    title="Apne stores jodiye"
    intro={demo ? "Demo mode mein dono stores sample data ke saath ready hain." : "Jo store judega, main wahan dhoondhungi aur prices compare karungi. Ek store bhi kaafi hai."}
    footer={anyConnected
      ? <button type="button" onClick={onContinue} className={primaryButton}>Aage badhein <ArrowRight size={17} weight="bold" /></button>
      : <button type="button" onClick={onSkip} disabled={checking} className={secondaryButton}>Abhi nahi, baad mein jodungi</button>}
  >
    {oauthReturned && stores[oauthReturned].connection === "connected" && <div role="status" className="mb-4 flex items-center gap-2 rounded-2xl bg-[#eef6ef] px-3.5 py-3 text-[13px] font-semibold text-[#2f6339]"><CheckCircle size={18} weight="fill" /> {storeName(oauthReturned)} jud gaya.</div>}
    <div className="space-y-3">
      {STORE_PROVIDERS.map((provider) => <StoreCard key={provider} provider={provider} state={stores[provider]} demo={demo} onConnect={() => onConnect(provider)} onRetryStatus={() => onRetryStatus(provider)} />)}
    </div>
    {!demo && <div className="mt-4"><Note icon={<LockKey size={17} weight="duotone" />}>Login store ke apne page par hota hai. Wahan sign in hote hi aapko yahin wapas laaya jayega. GharSaathi aapka password ya OTP kabhi nahi dekhti, na maangti hai.</Note></div>}
  </StepFrame>;
}

function StoreCard({ provider, state, demo, onConnect, onRetryStatus }: { provider: StoreProvider; state: StoreState; demo: boolean; onConnect: () => void; onRetryStatus: () => void }) {
  const name = storeName(provider);
  const connected = state.connection === "connected";
  const status = state.connection === "checking" ? "Check kar rahi hoon…" : demo ? "Demo ready" : connected ? "Juda hua" : state.connection === "error" ? "Status nahi mila" : "Nahi juda";
  return <section aria-label={name} className={`rounded-[22px] border p-4 transition-colors duration-200 ${connected ? "border-[#cfe2d2] bg-[#f6fbf6]" : "border-[#e7d8cd] bg-[#fffdf9]"}`}>
    <div className="flex items-center gap-3">
      <span className={`grid size-10 shrink-0 place-items-center rounded-xl ${connected ? "bg-[#dcefe0] text-[#397144]" : "bg-[#f7e8de] text-[#bd5525]"}`}>{connected ? <Check size={20} weight="bold" /> : <Storefront size={21} weight="duotone" />}</span>
      <div className="min-w-0 flex-1">
        <h2 className="text-[16px] font-semibold text-[#35241c]">{name}</h2>
        <p className={`text-[12px] font-semibold ${connected ? "text-[#397144]" : "text-[#8e796d]"}`}>{state.connection === "checking" ? <span className="inline-flex items-center gap-1.5"><Spinner size={12} /> {status}</span> : status}</p>
      </div>
    </div>
    {!connected && state.connection !== "checking" && <>
      <ul className="mt-3 space-y-1.5 text-[13px] leading-5 text-[#70584b]" aria-label={`${name} se kya dikhega`}>
        {STORE_ACCESS.map((item) => <li key={item} className="flex items-start gap-2"><Eye size={15} className="mt-0.5 shrink-0 text-[#a3887a]" aria-hidden="true" />{item}</li>)}
      </ul>
      {state.connection === "error"
        ? <InlineAlert action={<button type="button" onClick={onRetryStatus} className={`${textButton} -ml-2 mt-1`}><ArrowClockwise size={14} weight="bold" /> Phir check karein</button>}>{state.connectionError}</InlineAlert>
        : <button type="button" onClick={onConnect} disabled={state.connecting} className={`${provider === "zepto" ? primaryButton : secondaryButton} mt-4`}>{state.connecting ? <><Spinner /> Login page khol rahi hoon…</> : <>{name} se login karein <ArrowRight size={17} weight="bold" /></>}</button>}
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
    title="Delivery kahan karni hai?"
    intro={commerceMode === "demo" ? "Demo mein ek sample address hai. Asli address live mode mein dikhenge." : "Har store ke liye ek saved address chuniye. Isi address par prices aur delivery time check honge."}
    footer={<>
      {ready && missing.length > 0 && <p className="mb-3 text-center text-[12px] leading-5 text-[#806f65]">{missing.map(storeName).join(" aur ")} ka address baad mein bhi chuna ja sakta hai.</p>}
      {ready
        ? <button type="button" onClick={onContinue} disabled={busy} className={primaryButton}>{busy ? <><Spinner /> Save ho raha hai…</> : <>Yahi address rakhein <ArrowRight size={17} weight="bold" /></>}</button>
        : <button type="button" onClick={onSkip} className={secondaryButton}>Abhi skip karein</button>}
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
      <legend className="text-[15px] font-semibold text-[#3d2b23]">{name}</legend>
      {state.addressState === "ready" && state.addresses.length > 0 && !demo && <button type="button" onClick={() => onLoad(true)} className={`${textButton} ml-auto`}><ArrowClockwise size={14} weight="bold" /> Refresh</button>}
    </div>

    {loading && <div className="mt-2 space-y-2" aria-label={`${name} addresses load ho rahe hain`}>{[0, 1].map((item) => <div key={item} className="h-[68px] animate-pulse rounded-[20px] bg-[#eee2d8]" />)}</div>}

    {state.addressState === "needs_refresh" && <div className="mt-2 rounded-[20px] border border-[#e7d8cd] bg-[#fffdf9] p-4">
      <p className="text-[14px] leading-5 text-[#5f4a3f]">{name} ke saved addresses abhi yahan nahi hain. Ek baar laa leti hoon, phir yahin save rahenge.</p>
      <button type="button" onClick={() => onLoad(true)} className={`${secondaryButton} mt-3`}><MapPin size={17} weight="bold" /> {name} se addresses laayein</button>
    </div>}

    {state.addressState === "error" && <InlineAlert action={<button type="button" onClick={() => onLoad(false)} className={`${textButton} -ml-2 mt-1`}><ArrowClockwise size={14} weight="bold" /> Phir try karein</button>}>{state.addressError}</InlineAlert>}

    {state.addressState === "ready" && state.addresses.length > 0 && <div className="mt-2 overflow-hidden rounded-[20px] border border-[#e1d0c4] bg-[#fffdf9]">
      {state.addresses.map((address, index) => {
        const selected = state.selectedAddressId === address.id;
        return <label key={address.id} className={`flex min-h-[68px] cursor-pointer items-start gap-3 px-4 py-3.5 transition-colors duration-150 has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-inset has-[:focus-visible]:ring-[#c6531d] ${index ? "border-t border-[#f0e2d8]" : ""} ${selected ? "bg-[#fff4ec]" : "hover:bg-[#fffaf6]"}`}>
          <input type="radio" name={`${provider}-address`} value={address.id} checked={selected} disabled={state.selecting} onChange={() => onSelect(address.id)} className="sr-only" />
          <span aria-hidden="true" className={`mt-0.5 grid size-5 shrink-0 place-items-center rounded-full border-2 transition-colors duration-150 ${selected ? "border-[#c6531d] bg-[#c6531d]" : "border-[#cdb6a7] bg-white"}`}>{selected && <span className="size-1.5 rounded-full bg-white" />}</span>
          <span className="min-w-0 flex-1"><span className="block text-[14px] font-semibold text-[#3d2b23]">{address.tag || address.category || "Saved address"}</span><span className="mt-0.5 block text-[13px] leading-5 text-[#806f65]">{address.address_line}</span></span>
          {selected && state.selecting && <span className="mt-0.5 text-[#c6531d]"><Spinner size={15} /></span>}
        </label>;
      })}
    </div>}
    {state.addressState === "ready" && state.addresses.length > 0 && state.addressError && <InlineAlert>{state.addressError}</InlineAlert>}

    {state.addressState === "ready" && state.addresses.length === 0 && <div className="mt-2 rounded-[20px] border border-[#e7d8cd] bg-[#fffdf9] p-4">
      <p className="text-[14px] leading-5 text-[#5f4a3f]">{provider === "zepto" ? "Zepto par koi saved address nahi mila." : "Swiggy par koi saved address nahi mila. Swiggy app mein address jodkar yahan Refresh dabaiye."}</p>
      {provider === "swiggy" && <button type="button" onClick={() => onLoad(true)} className={`${textButton} -ml-2 mt-2`}><ArrowClockwise size={14} weight="bold" /> Refresh</button>}
    </div>}

    {provider === "zepto" && !demo && state.addressState === "ready" && (formOpen
      ? <ZeptoAddressForm onAdded={(data) => { onAdded(data); setFormOpen(false); }} onCancel={() => setFormOpen(false)} />
      : <button type="button" onClick={() => setFormOpen(true)} className={`${textButton} -ml-2 mt-2`}><Plus size={14} weight="bold" /> Naya Zepto address jodiye</button>)}
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
  if (history.permission === undefined) footer = <button type="button" disabled className={primaryButton}><Spinner /> Load ho raha hai…</button>;
  else if (demo) footer = <button type="button" onClick={() => onContinue("skipped")} className={primaryButton}>Aage badhein <ArrowRight size={17} weight="bold" /></button>;
  else if (importing) footer = <button type="button" disabled className={primaryButton}><Spinner /> Past orders padh rahi hoon…</button>;
  else if (finished || alreadyOn) footer = <button type="button" onClick={() => onContinue(anySucceeded || alreadyOn ? "done" : "skipped")} className={primaryButton}>Aage badhein <ArrowRight size={17} weight="bold" /></button>;
  else footer = <div className="grid gap-2.5">
    <button type="button" onClick={() => void history.enable()} disabled={history.busy !== null} className={primaryButton}>Haan, past orders import karein</button>
    <button type="button" onClick={() => void skip()} disabled={history.busy !== null} className={secondaryButton}>{history.busy === "skip" ? <><Spinner /> Save ho raha hai…</> : "Abhi nahi"}</button>
  </div>;

  return <StepFrame
    focusHeading={focusHeading}
    title={<>Kya main aapki <span className="whitespace-nowrap">&lsquo;usual&rsquo; cheezein</span> yaad rakhun?</>}
    intro="Pichhle orders dekhkar main pooch sakti hoon: “Wahi wala Amul doodh?”"
    footer={footer}
  >
    {history.permission === undefined
      ? <div className="space-y-3" aria-label="Load ho raha hai">{[0, 1, 2].map((item) => <div key={item} className="h-14 animate-pulse rounded-2xl bg-[#eee2d8]" />)}</div>
      : <>
        <ul className="divide-y divide-[#eaded5] border-y border-[#eaded5]">
          {[
            { icon: <Eye size={20} weight="duotone" />, title: "Sirf padhungi", text: "Connected stores ke recent orders, read-only. Isse kuch add ya order nahi hota." },
            { icon: <Sparkle size={20} weight="duotone" />, title: "“Wahi wala?” suggestions", text: "Repeat items pehchaan kar suggest karungi. Ghar mein kya khatam hua, iska andaaza nahi lagaungi." },
            { icon: <Trash size={20} weight="duotone" />, title: "Kabhi bhi delete", text: "Settings se band karte hi imported data hata diya jayega." },
          ].map((item) => <li key={item.title} className="flex items-start gap-3 py-3.5"><span className="mt-0.5 shrink-0 text-[#c6531d]">{item.icon}</span><span><span className="block text-[14px] font-semibold text-[#3d2b23]">{item.title}</span><span className="mt-0.5 block text-[13px] leading-5 text-[#806f65]">{item.text}</span></span></li>)}
        </ul>

        {demo && <div className="mt-5"><Note icon={<ShieldCheck size={17} weight="duotone" />}>Demo mode mein import band hai. Koi asli order history nahi padhi jaati.</Note></div>}

        {!demo && alreadyOn && <div role="status" className="mt-5 flex items-start gap-2.5 rounded-2xl bg-[#eef6ef] px-3.5 py-3 text-[13px] leading-5 text-[#2f6339]"><CheckCircle size={18} weight="fill" className="mt-0.5 shrink-0" /> Usuals pehle se on hain. Settings se kabhi bhi band kiya ja sakta hai.</div>}

        {!demo && !alreadyOn && !progressEntries.length && <fieldset className="mt-5" aria-describedby={ids.retention}>
          <legend className="text-[13px] font-semibold text-[#5f4a3f]">Kitne din tak yaad rakhun?</legend>
          <div className="mt-2 grid grid-cols-3 gap-2">
            {RETENTION_CHOICES.map((choice) => {
              const selected = history.retentionDays === choice.days;
              return <label key={choice.days} className={`flex min-h-12 cursor-pointer items-center justify-center rounded-2xl border text-[14px] font-semibold transition-[border-color,background-color,transform] duration-150 ease-out active:scale-[.97] has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-[#c6531d] ${selected ? "border-[#c6531d] bg-[#fff4ec] text-[#8f421b] ring-1 ring-[#c6531d]" : "border-[#e1d0c4] bg-[#fffdf9] text-[#5c463b]"}`}>
                <input type="radio" name="history-retention" value={choice.days} checked={selected} onChange={() => history.setRetentionDays(choice.days)} className="sr-only" />{choice.label}
              </label>;
            })}
          </div>
          <p id={ids.retention} className="mt-2 text-[12px] leading-5 text-[#8e796d]">Iske baad imported pattern data apne aap delete ho jayega.</p>
        </fieldset>}

        {progressEntries.length > 0 && <ul className="mt-5 overflow-hidden rounded-[20px] border border-[#e1d0c4] bg-[#fffdf9]" aria-label="Import progress" aria-live="polite">
          {progressEntries.map(([provider, item], index) => <li key={provider} className={`flex min-h-[60px] items-center gap-3 px-4 py-3 ${index ? "border-t border-[#f0e2d8]" : ""}`}>
            <span className={`grid size-8 shrink-0 place-items-center rounded-full ${item.state === "done" ? "bg-[#dcefe0] text-[#397144]" : item.state === "failed" ? "bg-[#fcedea] text-[#a33f31]" : "bg-[#f7e8de] text-[#bd5525]"}`}>
              {item.state === "done" ? <Check size={16} weight="bold" /> : item.state === "failed" ? <WarningCircle size={16} weight="fill" /> : item.state === "running" ? <Spinner size={15} /> : <Clock size={16} />}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[14px] font-semibold text-[#3d2b23]">{providerLabel(provider)}</span>
              <span className="block text-[12px] leading-5 text-[#806f65]">{item.state === "waiting" ? "Line mein hai" : item.state === "running" ? "Padh rahi hoon…" : item.state === "failed" ? item.message : item.result.cached ? "Haal hi mein check kiya tha, dobara call nahi kiya" : `${item.result.imported_orders} orders · ${item.result.imported_occurrences} items`}</span>
            </span>
          </li>)}
        </ul>}
        {finished && !anySucceeded && <p className="mt-3 text-[13px] leading-5 text-[#806f65]">Abhi import nahi ho paaya. Setup ke baad settings se dobara try kiya ja sakta hai.</p>}
        {history.error && !progressEntries.length && <InlineAlert action={history.loadError ? <button type="button" onClick={history.reload} className={`${textButton} -ml-2 mt-1`}><ArrowClockwise size={14} weight="bold" /> Phir try karein</button> : undefined}>{history.error}</InlineAlert>}
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
  const headline = byAddressForm(profile?.address_form, { masculine: "Aap ready ho gaye", feminine: "Aap ready ho gayi", neutral: "Sab ready hai" });

  async function finish() {
    setSaving(true);
    setError("");
    try {
      await onFinish();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Setup save nahi hua. Ek baar phir try kijiye.");
      setSaving(false);
    }
  }

  return <StepFrame
    focusHeading={focusHeading}
    title={<>{headline}{name ? `, ${name}` : ""}.</>}
    intro={connectedStores.length ? "Ab bas boliye. Baaki main dekh loongi, aur order se pehle aapse poochungi." : "Store jodte hi main shopping shuru kar sakti hoon. Tab tak chat try kijiye."}
    footer={<>
      {error && <InlineAlert>{error}</InlineAlert>}
      <button type="button" onClick={() => void finish()} disabled={saving} className={`${primaryButton} ${error ? "mt-3" : ""}`}>{saving ? <><Spinner /> Ek second…</> : <>{revisit ? "Wapas shopping par" : "Shopping shuru karein"} <ArrowRight size={17} weight="bold" /></>}</button>
    </>}
  >
    <div className="rounded-[24px] bg-[#3b2a22] p-5 text-[#fff9f4] shadow-[0_24px_50px_-30px_rgba(53,36,28,.9)]">
      <div className="flex items-center gap-4">
        <motion.span
          initial={reduceMotion ? false : { scale: 0.9, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ type: "spring", duration: 0.45, bounce: 0.2, delay: 0.1 }}
          className="relative grid size-14 shrink-0 place-items-center rounded-full bg-[#c6531d] text-white shadow-[0_12px_30px_-14px_rgba(198,83,29,.9)]"
          aria-hidden="true"
        ><Microphone size={24} weight="fill" /></motion.span>
        <p className="text-[14px] leading-5 text-[#ead8cb]">Mic dabaiye aur boliye:</p>
      </div>
      <p className="font-display mt-4 text-[24px] font-semibold leading-tight tracking-[-.03em]">&ldquo;Doodh aur atta chahiye&rdquo;</p>
    </div>

    <dl className="mt-5 divide-y divide-[#eaded5] border-y border-[#eaded5] text-[14px]">
      <div className="flex items-center justify-between gap-3 py-3"><dt className="text-[#806f65]">Stores</dt><dd className="text-right font-semibold text-[#3d2b23]">{commerceMode === "demo" ? "Demo (Zepto + Swiggy)" : connectedStores.length ? connectedStores.map(storeName).join(", ") : "Abhi koi nahi"}</dd></div>
      {profile && <div className="flex items-center justify-between gap-3 py-3"><dt className="text-[#806f65]">Reply language</dt><dd className="font-semibold text-[#3d2b23]">{LANGUAGE_NAMES[profile.language]}</dd></div>}
      <div className="flex items-center justify-between gap-3 py-3"><dt className="text-[#806f65]">Order</dt><dd className="font-semibold text-[#3d2b23]">Sirf aapki approval se</dd></div>
    </dl>
  </StepFrame>;
}
