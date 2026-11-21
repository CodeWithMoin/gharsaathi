"use client";

import { SignInButton, SignUpButton, useUser } from "@clerk/nextjs";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowLeft, ArrowRight, BellRinging, Brain, Calculator, Check, Microphone } from "@phosphor-icons/react";
import Image from "next/image";
import { FormEvent, ReactNode, useMemo, useState } from "react";

type Language = "hinglish" | "hindi" | "english";

const languageOptions: Array<{ value: Language; label: string; note: string }> = [
  { value: "hinglish", label: "Hinglish", note: "Recommended" },
  { value: "hindi", label: "Hindi", note: "हिंदी" },
  { value: "english", label: "English", note: "English" },
];

function LogoMark({ size = "large" }: { size?: "large" | "small" }) {
  return <span className={`${size === "large" ? "size-[88px] rounded-[28px]" : "size-14 rounded-[18px]"} block overflow-hidden border border-[#e7cbb8] bg-white shadow-[0_22px_55px_-32px_rgba(101,52,27,.65)]`}>
    <Image className="size-full scale-[1.08] object-cover" src="/gharsaathi-icon.png" alt="GharSaathi" width={96} height={96} priority />
  </span>;
}

function LoadingSplash() {
  return <main className="grain grid min-h-[100dvh] place-items-center bg-[#f8f2ea] px-6 text-[#35241c]">
    <div className="flex flex-col items-center">
      <LogoMark />
      <p className="font-display mt-5 text-2xl font-semibold tracking-[-.04em]">GharSaathi</p>
      <span className="mt-5 h-1 w-12 overflow-hidden rounded-full bg-[#ead8ca]"><span className="block h-full w-1/2 animate-pulse rounded-full bg-[#c6531d]" /></span>
    </div>
  </main>;
}

const featureSlides = [
  {
    eyebrow: "YOUR HOUSEHOLD",
    title: "Aapki pasand, yaad.",
    description: "Aapki permission se brand, pack size aur quantity yaad rahenge.",
    icon: <Brain size={38} weight="duotone" />,
  },
  {
    eyebrow: "SMART COMPARISON",
    title: "Sahi basket, ek baar mein.",
    description: "Swiggy aur Zepto par stock, fees, offers aur delivery milaakar compare.",
    icon: <Calculator size={38} weight="duotone" />,
  },
  {
    eyebrow: "AFTER YOU ORDER",
    title: "Order ka update, yahin.",
    description: "Delivery changes aur issue details isi chat mein. Support ke liye context ready.",
    icon: <BellRinging size={38} weight="duotone" />,
  },
];

export function PublicOnboarding() {
  const [step, setStep] = useState(0);
  const authStep = featureSlides.length + 1;
  const isFeature = step > 0 && step < authStep;
  const feature = isFeature ? featureSlides[step - 1] : null;

  return <main className="grain min-h-[100dvh] bg-[#f8f2ea] px-6 text-[#35241c]">
    <div className="mx-auto flex min-h-[100dvh] w-full max-w-sm flex-col pb-[max(1.75rem,env(safe-area-inset-bottom))] pt-[max(2rem,env(safe-area-inset-top))]">
      <div className="flex h-10 items-center justify-between">
        {step > 0 ? <button type="button" onClick={() => setStep((current) => current - 1)} aria-label="Previous onboarding screen" className="grid size-10 place-items-center rounded-full border border-[#e1d0c4] bg-[#fffdf9] text-[#70584b] active:scale-[.96]"><ArrowLeft size={18} /></button> : <span />}
        {isFeature && <button type="button" onClick={() => setStep(authStep)} className="px-2 py-2 text-xs font-semibold text-[#806f65]">Skip</button>}
      </div>

      <AnimatePresence mode="wait" initial={false}>
        {step === 0 && <motion.section key="intro" initial={{ opacity: 0, x: 18 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -18 }} transition={{ duration: .22 }} className="flex flex-1 flex-col items-center justify-center pb-2 text-center">
          <LogoMark />
          <p className="font-display mt-5 text-[30px] font-semibold tracking-[-.05em]">GharSaathi</p>
          <h1 className="mt-8 text-[30px] font-semibold leading-[1.12] tracking-[-.045em]">Ghar ka shopping,<br />bas bol dijiye.</h1>
          <p className="mt-4 max-w-[28ch] text-[15px] leading-6 text-[#806f65]">Aap bolenge. Main choices samajhkar options laungi. Final decision hamesha aapka.</p>
        </motion.section>}

        {feature && <motion.section key={feature.eyebrow} initial={{ opacity: 0, x: 24 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -24 }} transition={{ duration: .22 }} className="flex min-h-0 flex-1 items-center justify-center text-center">
          <div className="flex w-full -translate-y-2 flex-col items-center">
            <div className="grid size-20 place-items-center rounded-[26px] border border-[#e4c9b8] bg-[#fff8f2] text-[#c6531d] shadow-[0_24px_50px_-34px_rgba(101,52,27,.65)]">{feature.icon}</div>
            <p className="mt-7 text-[10px] font-semibold tracking-[.18em] text-[#c6531d]">{feature.eyebrow}</p>
            <h1 className="font-display mt-3 max-w-[12ch] text-[36px] font-semibold leading-[1.08] tracking-[-.05em]">{feature.title}</h1>
            <p className="mt-4 max-w-[29ch] text-[15px] leading-6 text-[#806f65]">{feature.description}</p>
          </div>
        </motion.section>}

        {step === authStep && <motion.section key="auth" initial={{ opacity: 0, x: 24 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -24 }} transition={{ duration: .22 }} className="flex min-h-0 flex-1 flex-col text-center">
          <div className="flex min-h-0 flex-1 flex-col items-center justify-center pb-2">
            <LogoMark />
            <h1 className="font-display mt-7 text-[34px] font-semibold leading-[1.1] tracking-[-.05em]">Apna GharSaathi<br />ready karein.</h1>
            <p className="mt-4 max-w-[30ch] text-sm leading-6 text-[#806f65]">Secure account se chats, preferences, baskets aur approvals sirf aapke rahenge.</p>
          </div>
          <div className="w-full pt-6">
            <SignUpButton mode="redirect" forceRedirectUrl="/">
              <button className="flex w-full items-center justify-center gap-2 rounded-full bg-[#c6531d] px-5 py-3.5 text-sm font-semibold text-white shadow-[0_18px_38px_-20px_rgba(158,65,24,.8)] transition active:scale-[.98]">Create account <ArrowRight size={17} weight="bold" /></button>
            </SignUpButton>
            <SignInButton mode="redirect" forceRedirectUrl="/">
              <button className="mt-3 w-full rounded-full border border-[#ddc8ba] bg-[#fffdf9] px-5 py-3.5 text-sm font-semibold text-[#5c463b] transition active:scale-[.98]">I already have an account</button>
            </SignInButton>
            <p className="mt-5 text-[10px] leading-4 text-[#9a8579]">Secure sign-in by Clerk · Passwords and OTPs never enter GharSaathi chat</p>
          </div>
        </motion.section>}
      </AnimatePresence>

      {step < authStep && <div className="mt-auto pt-8">
        <div className="mb-5 flex justify-center gap-1.5" aria-label={`Onboarding screen ${step + 1} of ${authStep}`}>
          {Array.from({ length: authStep }, (_, index) => <span key={index} className={`h-1.5 rounded-full transition-all ${index === step ? "w-6 bg-[#c6531d]" : "w-1.5 bg-[#d9c7bb]"}`} />)}
        </div>
        <button type="button" onClick={() => setStep((current) => Math.min(authStep, current + 1))} className="flex w-full items-center justify-center gap-2 rounded-full bg-[#c6531d] px-5 py-3.5 text-sm font-semibold text-white shadow-[0_18px_38px_-20px_rgba(158,65,24,.8)] transition active:scale-[.98]">{step === 0 ? "See how it works" : step === authStep - 1 ? "Continue" : "Next"} <ArrowRight size={17} weight="bold" /></button>
      </div>}
    </div>
  </main>;
}

export function FirstRunOnboarding({ children }: { children: (profile: { justCompleted: boolean; firstName: string }) => ReactNode }) {
  const { isLoaded, isSignedIn, user } = useUser();
  const savedProfile = useMemo(() => user?.unsafeMetadata.gharsaathiProfile as { language?: Language; complete?: boolean } | undefined, [user?.unsafeMetadata]);
  const [name, setName] = useState("");
  const [language, setLanguage] = useState<Language>("hinglish");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [justCompleted, setJustCompleted] = useState(false);

  if (!isLoaded) return <LoadingSplash />;
  if (!isSignedIn || !user) return <PublicOnboarding />;
  if (savedProfile?.complete || justCompleted) return <>{children({ justCompleted, firstName: user.firstName?.trim() || "there" })}</>;

  const activeUser = user;
  const displayName = name || activeUser.firstName || "";

  async function completeProfile(event: FormEvent) {
    event.preventDefault();
    const firstName = displayName.trim();
    if (!firstName) {
      setError("Naam bata dijiye. Main isi naam se bulaungi.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await activeUser.update({
        firstName,
        unsafeMetadata: {
          ...activeUser.unsafeMetadata,
          gharsaathiProfile: { complete: true, language, completedAt: new Date().toISOString() },
        },
      });
      setJustCompleted(true);
    } catch {
      setError("Profile save nahi hua. Ek baar phir try karein.");
    } finally {
      setSaving(false);
    }
  }

  return <main className="grain min-h-[100dvh] bg-[#f8f2ea] px-5 text-[#35241c]">
    <div className="mx-auto flex min-h-[100dvh] w-full max-w-sm flex-col pb-[max(1.75rem,env(safe-area-inset-bottom))] pt-[max(3rem,env(safe-area-inset-top))]">
      <div className="flex items-center justify-between">
        <LogoMark size="small" />
        <span className="text-[10px] font-semibold uppercase tracking-[.16em] text-[#9b8172]">1 of 2 · Your profile</span>
      </div>

      <div className="mt-12">
        <h1 className="font-display whitespace-nowrap text-[32px] font-semibold leading-[1.1] tracking-[-.05em] sm:text-[34px]">Aapko kya bulaun?</h1>
        <p className="mt-3 max-w-[30ch] text-sm leading-6 text-[#806f65]">Bas naam aur preferred language. Stores aur order-history consent agle step mein honge.</p>
      </div>

      <form onSubmit={completeProfile} className="mt-10 flex flex-1 flex-col">
        <label htmlFor="first-name" className="text-xs font-semibold text-[#70584b]">Your name</label>
        <input id="first-name" value={displayName} onChange={(event) => setName(event.target.value)} autoComplete="given-name" autoFocus className="mt-2 w-full rounded-2xl border border-[#ddc8ba] bg-[#fffdf9] px-4 py-3.5 text-base outline-none transition placeholder:text-[#aa978d] focus:border-[#c6531d] focus:ring-2 focus:ring-[#c6531d]/15" placeholder="Your name" />

        <fieldset className="mt-7">
          <legend className="text-xs font-semibold text-[#70584b]">I prefer</legend>
          <div className="mt-2 grid grid-cols-3 gap-2">
            {languageOptions.map((option) => {
              const selected = language === option.value;
              return <button key={option.value} type="button" onClick={() => setLanguage(option.value)} aria-pressed={selected} className={`relative min-h-[72px] rounded-2xl border px-2 py-3 text-left transition active:scale-[.98] ${selected ? "border-[#c6531d] bg-[#fff7f1] text-[#8f421b] ring-1 ring-[#c6531d]" : "border-[#e1d0c4] bg-[#fffdf9] text-[#70584b]"}`}>
                <span className="block text-xs font-semibold">{option.label}</span>
                <span className="mt-1 block text-[9px] text-[#9a8579]">{option.note}</span>
                {selected && <Check size={14} weight="bold" className="absolute right-2 top-2 text-[#c6531d]" />}
              </button>;
            })}
          </div>
        </fieldset>

        <div className="mt-7 flex items-start gap-2 rounded-2xl bg-[#f1e8e0] px-3.5 py-3 text-[11px] leading-5 text-[#806f65]"><Microphone size={16} className="mt-0.5 shrink-0 text-[#c6531d]" />You can speak naturally and change this later.</div>
        {error && <p className="mt-3 text-xs font-medium text-[#a33f31]" role="alert">{error}</p>}

        <button type="submit" disabled={saving} className="mt-auto flex w-full items-center justify-center gap-2 rounded-full bg-[#c6531d] px-5 py-3.5 text-sm font-semibold text-white shadow-[0_18px_38px_-20px_rgba(158,65,24,.8)] transition active:scale-[.98] disabled:cursor-not-allowed disabled:opacity-55">{saving ? "Saving…" : "Continue to stores"} {!saving && <ArrowRight size={17} weight="bold" />}</button>
      </form>
    </div>
  </main>;
}
