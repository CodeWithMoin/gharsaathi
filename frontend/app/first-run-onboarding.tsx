"use client";

import { SignInButton, SignUpButton, useUser } from "@clerk/nextjs";
import { ArrowRight, Microphone, Scales, ShieldCheck } from "@phosphor-icons/react";
import { motion, useReducedMotion } from "framer-motion";
import Image from "next/image";
import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useApiRequest } from "./authenticated-api";
import {
  canSkip,
  isOnboardingComplete,
  nextOnboardingStep,
  progressPosition,
  readOnboardingProgress,
  resolveProfile,
  stepAfter,
  stepBefore,
  withOnboardingProgress,
  type DecisionStep,
  type HouseholdProfile,
  type OnboardingProgress,
  type OnboardingSnapshot,
  type OnboardingStep,
  type StepDecision,
} from "./onboarding-flow.mjs";
import { AddressStep, ConsentStep, DoneStep, OnboardingHeader, PersonalizeStep, ProfileStep, STEP_TITLES, StepTransition, StoresStep } from "./onboarding-steps";
import { useProfileApi } from "./profile-api";
import { STORE_PROVIDERS, useStoreSetup, type StoreProvider } from "./store-setup";

type LegalConsentStatus = {
  terms_version: string;
  accepted: boolean;
  accepted_at: string | null;
  personalization_allowed: boolean;
  personalization_decided_at: string | null;
};

type Metadata = Record<string, unknown>;

/** Survives the provider OAuth round-trip so a settings revisit resumes where it left off. */
const REVISIT_FLAG = "gharsaathi:onboarding:revisit";
const EASE_OUT = [0.23, 1, 0.32, 1] as const;

function LogoMark({ size = "large" }: { size?: "large" | "small" }) {
  return <span className={`${size === "large" ? "size-[72px] rounded-[24px]" : "size-12 rounded-[16px]"} block overflow-hidden border border-[#e7cbb8] bg-white shadow-[0_22px_55px_-32px_rgba(101,52,27,.65)]`}>
    <Image className="size-full scale-[1.08] object-cover" src="/gharsaathi-icon.png" alt="" width={96} height={96} priority />
  </span>;
}

function LoadingSplash() {
  return <main className="grain grid min-h-[100dvh] place-items-center bg-[#f8f2ea] px-6 text-[#35241c]" aria-busy="true">
    <div className="flex flex-col items-center">
      <LogoMark />
      <p className="font-display mt-5 text-2xl font-semibold tracking-[-.04em]">GharSaathi</p>
      <span className="mt-5 h-1 w-12 overflow-hidden rounded-full bg-[#ead8ca]" role="progressbar" aria-label="GharSaathi load ho raha hai"><span className="block h-full w-1/2 animate-pulse rounded-full bg-[#c6531d]" /></span>
    </div>
  </main>;
}

const PROMISES = [
  { icon: <Microphone size={20} weight="duotone" />, title: "Bolkar mangwaiye", text: "Hinglish, Hindi ya English, jo aasaan lage." },
  { icon: <Scales size={20} weight="duotone" />, title: "Har store ka asli total", text: "Price, fees aur delivery time ek saath." },
  { icon: <ShieldCheck size={20} weight="duotone" />, title: "Aapki approval ke bina kuch order nahi", text: "Har basket aur bill pehle aapko dikhaungi." },
];

/** Signed-out welcome: what GharSaathi does, the safety promise, then Clerk sign-up/in. */
export function PublicOnboarding() {
  const [legalAccepted, setLegalAccepted] = useState(false);
  const reduceMotion = useReducedMotion();
  const rise = (index: number) => reduceMotion
    ? { initial: { opacity: 0 }, animate: { opacity: 1 }, transition: { duration: 0.2 } }
    : { initial: { opacity: 0, y: 10 }, animate: { opacity: 1, y: 0 }, transition: { duration: 0.32, ease: EASE_OUT, delay: 0.04 + index * 0.05 } };

  return <main className="grain min-h-[100dvh] bg-[#f8f2ea] px-5 text-[#35241c]">
    <div className="mx-auto flex min-h-[100dvh] w-full max-w-sm flex-col pb-[max(1.5rem,env(safe-area-inset-bottom))] pt-[max(2.5rem,env(safe-area-inset-top))]">
      <motion.div {...rise(0)}><LogoMark /></motion.div>
      <motion.h1 {...rise(1)} className="font-display mt-7 text-[34px] font-semibold leading-[1.08] tracking-[-.05em]">Ghar ka shopping,<br />ab bas bolkar.</motion.h1>
      <motion.p {...rise(2)} className="mt-3 max-w-[32ch] text-[15px] leading-6 text-[#806f65]">Main GharSaathi hoon. Zepto aur Swiggy par dhoondhti, compare karti aur basket taiyaar rakhti hoon.</motion.p>

      <motion.ul {...rise(3)} className="mt-7 divide-y divide-[#eaded5] border-y border-[#eaded5]">
        {PROMISES.map((item) => <li key={item.title} className="flex items-start gap-3 py-3.5">
          <span className="mt-0.5 shrink-0 text-[#c6531d]">{item.icon}</span>
          <span><span className="block text-[14px] font-semibold text-[#3d2b23]">{item.title}</span><span className="mt-0.5 block text-[13px] leading-5 text-[#806f65]">{item.text}</span></span>
        </li>)}
      </motion.ul>

      <motion.div {...rise(4)} className="mt-auto pt-8">
        <label className="flex cursor-pointer items-start gap-2.5 rounded-2xl bg-[#f1e8e0] px-3.5 py-3 text-left text-[13px] leading-5 text-[#70584b] has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-[#c6531d]">
          <input type="checkbox" checked={legalAccepted} onChange={(event) => setLegalAccepted(event.target.checked)} className="mt-0.5 size-4 shrink-0 accent-[#c6531d]" />
          <span>I agree to GharSaathi&apos;s <a href="/terms" className="font-semibold text-[#9d421a] underline underline-offset-2">Terms</a> and <a href="/privacy" className="font-semibold text-[#9d421a] underline underline-offset-2">Privacy Policy</a>.</span>
        </label>
        <SignUpButton mode="redirect" forceRedirectUrl="/">
          <button type="button" disabled={!legalAccepted} className="mt-3 flex min-h-[52px] w-full items-center justify-center gap-2 rounded-full bg-[#c6531d] px-5 text-[15px] font-semibold text-white shadow-[0_18px_38px_-20px_rgba(158,65,24,.8)] transition-[transform,background-color,opacity] duration-150 ease-out hover:bg-[#b94a18] active:scale-[.97] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#c6531d] focus-visible:ring-offset-2 focus-visible:ring-offset-[#f8f2ea] disabled:cursor-not-allowed disabled:opacity-50">Account banaiye <ArrowRight size={17} weight="bold" /></button>
        </SignUpButton>
        <SignInButton mode="redirect" forceRedirectUrl="/">
          <button type="button" className="mt-2.5 min-h-[52px] w-full rounded-full border border-[#ddc8ba] bg-[#fffdf9] px-5 text-[15px] font-semibold text-[#5c463b] transition-[transform,background-color] duration-150 ease-out hover:bg-[#fff7f1] active:scale-[.97] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#c6531d] focus-visible:ring-offset-2 focus-visible:ring-offset-[#f8f2ea]">Mera account pehle se hai</button>
        </SignInButton>
        <p className="mt-3 text-center text-[12px] leading-5 text-[#8e796d]">Secure sign-in. Password aur OTP kabhi chat mein nahi.</p>
      </motion.div>
    </div>
  </main>;
}

function readOAuthReturn(): StoreProvider | null {
  if (typeof window === "undefined") return null;
  const params = new URL(window.location.href).searchParams;
  return STORE_PROVIDERS.find((provider) => params.get(provider) === "connected") ?? null;
}

function clearOAuthReturn() {
  const url = new URL(window.location.href);
  for (const provider of STORE_PROVIDERS) url.searchParams.delete(provider);
  window.history.replaceState({}, "", url);
}

type AppContext = { justCompleted: false; firstName: string; openSetup: () => void };

/**
 * Signed-in gate. Sends new users through setup, resumes half-finished users at
 * their first incomplete step, and lets completed users revisit it from settings.
 */
export function FirstRunOnboarding({ children }: { children: (context: AppContext) => ReactNode }) {
  const { isLoaded, isSignedIn, user } = useUser();
  const request = useApiRequest();
  const { loadProfile, saveProfile } = useProfileApi();

  const [consent, setConsent] = useState<LegalConsentStatus | null>(null);
  const [consentError, setConsentError] = useState("");
  const [consentRetry, setConsentRetry] = useState(0);
  const [serverProfile, setServerProfile] = useState<HouseholdProfile | null | undefined>(undefined);
  const [mode, setMode] = useState<"pending" | "first-run" | "revisit" | "app">("pending");
  const [step, setStep] = useState<OnboardingStep | null>(null);
  const [direction, setDirection] = useState<1 | -1>(1);
  const [navigated, setNavigated] = useState(false);
  const [progress, setProgress] = useState<OnboardingProgress | null>(null);
  const [oauthReturn] = useState(readOAuthReturn);

  const metadataRef = useRef<Metadata | null>(null);
  const [savedMetadata, setSavedMetadata] = useState<Metadata | null>(null);
  const metadataQueue = useRef<Promise<unknown>>(Promise.resolve());
  const userRef = useRef(user);
  useEffect(() => { userRef.current = user; }, [user]);

  const userId = user?.id;
  useEffect(() => {
    if (!isLoaded || !isSignedIn || !userId) return;
    let active = true;
    setConsent(null);
    setConsentError("");
    void request<LegalConsentStatus>("/api/legal-consent")
      .then((status) => { if (active) setConsent(status); })
      .catch((cause: unknown) => { if (active) setConsentError(cause instanceof Error ? cause.message : "Could not check your consent status."); });
    return () => { active = false; };
  }, [consentRetry, isLoaded, isSignedIn, request, userId]);

  useEffect(() => {
    if (!isLoaded || !isSignedIn || !userId) return;
    let active = true;
    void loadProfile().then((result) => { if (active) setServerProfile(result.profile); });
    return () => { active = false; };
  }, [isLoaded, isSignedIn, loadProfile, userId]);

  const userMetadata = user?.unsafeMetadata;
  const metadata = useMemo(() => (savedMetadata ?? userMetadata ?? {}) as Metadata, [savedMetadata, userMetadata]);
  const resolved = useMemo(() => resolveProfile({ server: serverProfile, clerkMetadata: metadata, clerkFirstName: user?.firstName }), [metadata, serverProfile, user?.firstName]);
  const currentProgress = progress ?? readOnboardingProgress(metadata);
  const onboardingActive = mode === "first-run" || mode === "revisit";
  const needsStoreState = onboardingActive || (mode === "pending" && consent !== null && serverProfile !== undefined && !(isOnboardingComplete(currentProgress) && consent.accepted));
  const storeSetup = useStoreSetup(Boolean(isSignedIn) && needsStoreState);
  const { stores, commerceMode } = storeSetup;
  const storesSettled = STORE_PROVIDERS.every((provider) => stores[provider].connection !== "checking");
  const connected = { swiggy: stores.swiggy.connection === "connected", zepto: stores.zepto.connection === "connected" };

  const snapshot: OnboardingSnapshot = {
    profileComplete: Boolean(resolved.profile),
    consentAccepted: Boolean(consent?.accepted),
    stores: connected,
    progress: currentProgress,
    oauthReturn: Boolean(oauthReturn),
  };

  // Decide once where to land: the app, or the first incomplete step.
  useEffect(() => {
    if (mode !== "pending" || !consent || serverProfile === undefined) return;
    const revisitPending = typeof window !== "undefined" && window.sessionStorage.getItem(REVISIT_FLAG) === "1";
    if (oauthReturn && revisitPending && consent.accepted) {
      window.sessionStorage.removeItem(REVISIT_FLAG);
      clearOAuthReturn();
      setMode("revisit");
      setStep("stores");
      return;
    }
    if (!oauthReturn && revisitPending) window.sessionStorage.removeItem(REVISIT_FLAG);
    const firstGuess = nextOnboardingStep({ ...snapshot, stores: {} });
    if (firstGuess === null) { setMode("app"); return; }
    // Only steps after consent depend on store state; wait for it before resuming there.
    const needsStores = firstGuess !== "profile" && firstGuess !== "consent";
    if (needsStores && !storesSettled) return;
    const next = nextOnboardingStep(snapshot);
    if (next === null) { setMode("app"); return; }
    if (oauthReturn) clearOAuthReturn();
    setMode("first-run");
    setStep(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, consent, serverProfile, storesSettled, oauthReturn]);

  /** Serialises Clerk metadata writes so concurrent updates never clobber each other. */
  const writeMetadata = useCallback((build: (current: Metadata) => Metadata, extra?: { firstName?: string }) => {
    const run = metadataQueue.current.catch(() => undefined).then(async () => {
      const activeUser = userRef.current;
      if (!activeUser) throw new Error("Please sign in to continue.");
      const current = (metadataRef.current ?? activeUser.unsafeMetadata ?? {}) as Metadata;
      const next = build(current);
      const updated = await activeUser.update({ ...extra, unsafeMetadata: next });
      metadataRef.current = (updated?.unsafeMetadata ?? next) as Metadata;
      setSavedMetadata(metadataRef.current);
    });
    metadataQueue.current = run;
    return run;
  }, []);

  const recordDecision = useCallback((decisionStep: DecisionStep, decision: StepDecision) => {
    setProgress((current) => {
      const base = current ?? readOnboardingProgress(metadataRef.current ?? userRef.current?.unsafeMetadata);
      return { ...base, decisions: { ...base.decisions, [decisionStep]: decision } };
    });
    // Best effort: a failed write only means this step is offered again next time.
    void writeMetadata((current) => withOnboardingProgress(current, { step: decisionStep, decision })).catch(() => undefined);
  }, [writeMetadata]);

  function goTo(next: OnboardingStep | null, dir: 1 | -1 = 1) {
    if (!next) return;
    setDirection(dir);
    setNavigated(true);
    setStep(next);
    if (typeof window !== "undefined") window.scrollTo({ top: 0 });
  }

  const advance = (from: OnboardingStep, overrides?: Partial<OnboardingSnapshot>) => goTo(stepAfter(from, { ...snapshot, ...overrides }), 1);

  async function saveProfileStep(profile: HouseholdProfile) {
    await saveProfile(profile);
    // The Clerk copy remains the fallback (and feeds `user.firstName`) until /api/profile ships everywhere.
    await writeMetadata((current) => {
      const saved = (current.gharsaathiProfile ?? {}) as Metadata;
      return { ...current, gharsaathiProfile: { ...saved, firstName: profile.first_name, addressForm: profile.address_form, language: profile.language } };
    }, { firstName: profile.first_name });
    setServerProfile(profile);
    advance("profile", { profileComplete: true });
  }

  async function acceptConsent(personalizationAllowed: boolean) {
    const status = await request<LegalConsentStatus>("/api/legal-consent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ terms_accepted: true, personalization_allowed: personalizationAllowed }),
    });
    setConsent(status);
    if (isOnboardingComplete(currentProgress) && mode === "first-run") {
      // A returning user who only needed to accept a new terms version.
      setMode("app");
      return;
    }
    advance("consent", { consentAccepted: true });
  }

  async function finish() {
    if (mode !== "revisit") {
      const completedAt = new Date().toISOString();
      await writeMetadata((current) => withOnboardingProgress(current, { completedAt }));
      setProgress((current) => ({ ...(current ?? readOnboardingProgress(metadataRef.current)), completedAt }));
    }
    setMode("app");
  }

  function connectStore(provider: StoreProvider) {
    if (mode === "revisit") window.sessionStorage.setItem(REVISIT_FLAG, "1");
    void storeSetup.connect(provider);
  }

  function skipCurrent() {
    if (!step || !canSkip(step)) return;
    if (step === "stores" || step === "address" || step === "personalize") recordDecision(step, "skipped");
    advance(step);
  }

  const openSetup = useCallback(() => {
    setNavigated(false);
    setDirection(1);
    setMode("revisit");
    setStep("profile");
  }, []);

  if (!isLoaded) return <LoadingSplash />;
  if (!isSignedIn || !user) return <PublicOnboarding />;

  if (consentError) {
    return <main className="grain grid min-h-[100dvh] place-items-center bg-[#f8f2ea] px-5 text-center text-[#35241c]">
      <section role="alert" className="max-w-sm rounded-3xl border border-[#e1d0c4] bg-[#fffdf9] p-6 shadow-[0_22px_55px_-32px_rgba(101,52,27,.45)]">
        <h1 className="font-display text-2xl font-semibold">Consent status nahi mila</h1>
        <p className="mt-3 text-sm leading-6 text-[#806f65]">{consentError}</p>
        <button type="button" onClick={() => setConsentRetry((value) => value + 1)} className="mt-6 min-h-11 rounded-full bg-[#c6531d] px-5 text-sm font-semibold text-white transition-transform duration-150 ease-out active:scale-[.97] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#c6531d] focus-visible:ring-offset-2">Phir try karein</button>
      </section>
    </main>;
  }

  if (mode === "app") {
    return <>{children({ justCompleted: false, firstName: resolved.profile?.first_name || user.firstName?.trim() || "there", openSetup })}</>;
  }
  if (mode === "pending" || !step) return <LoadingSplash />;

  const connectedStores = STORE_PROVIDERS.filter((provider) => connected[provider]);
  const termsOnly = step === "consent" && isOnboardingComplete(currentProgress);
  const back = termsOnly || (step === "done" && mode !== "revisit") ? null : stepBefore(step, snapshot);
  const focusHeading = navigated;

  return <main className="grain min-h-[100dvh] bg-[#f8f2ea] text-[#35241c]">
    <div className="mx-auto flex min-h-[100dvh] w-full max-w-sm flex-col px-5 pt-[max(1rem,env(safe-area-inset-top))]">
      <OnboardingHeader
        position={progressPosition(step)}
        stepLabel={STEP_TITLES[step]}
        onBack={back ? () => goTo(back, -1) : undefined}
        onSkip={canSkip(step) ? skipCurrent : undefined}
        onClose={mode === "revisit" ? () => setMode("app") : undefined}
      />
      <StepTransition stepKey={step} direction={direction}>
        {step === "profile" && <ProfileStep initial={resolved.draft} focusHeading={focusHeading} onSave={saveProfileStep} />}
        {step === "consent" && <ConsentStep updatedTerms={isOnboardingComplete(currentProgress)} focusHeading={focusHeading} onAccept={acceptConsent} />}
        {step === "stores" && <StoresStep
          stores={stores}
          commerceMode={commerceMode}
          focusHeading={focusHeading}
          oauthReturned={oauthReturn}
          onConnect={connectStore}
          onRetryStatus={(provider) => void storeSetup.checkConnection(provider)}
          onContinue={() => { recordDecision("stores", "done"); advance("stores"); }}
          onSkip={skipCurrent}
        />}
        {step === "address" && <AddressStep
          stores={stores}
          commerceMode={commerceMode}
          focusHeading={focusHeading}
          onLoad={storeSetup.loadAddresses}
          onSelect={(provider, id) => void storeSetup.selectAddress(provider, id)}
          onAddressesAdded={storeSetup.applyAddresses}
          onContinue={() => { recordDecision("address", "done"); advance("address"); }}
          onSkip={skipCurrent}
        />}
        {step === "personalize" && <PersonalizeStep
          swiggyConnected={connected.swiggy}
          zeptoConnected={connected.zepto}
          commerceMode={commerceMode}
          focusHeading={focusHeading}
          onContinue={(decision) => { recordDecision("personalize", decision); advance("personalize"); }}
        />}
        {step === "done" && <DoneStep profile={resolved.profile} connectedStores={connectedStores} commerceMode={commerceMode} focusHeading={focusHeading} revisit={mode === "revisit"} onFinish={finish} />}
      </StepTransition>
    </div>
  </main>;
}
