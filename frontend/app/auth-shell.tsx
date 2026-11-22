import Image from "next/image";
import { ReactNode } from "react";
import { CLERK_COLORS } from "./ui/clerk-colors.mjs";

export const clerkAppearance = {
  variables: {
    ...CLERK_COLORS,
    borderRadius: "1.25rem",
    fontFamily: '"DM Sans", sans-serif',
  },
  elements: {
    rootBox: "w-full",
    cardBox: "w-full shadow-none",
    card: "w-full rounded-[28px] border border-border-strong bg-surface shadow-[0_28px_70px_-48px_rgb(var(--shadow)/.65)]",
    headerTitle: "font-display tracking-[-.035em] text-ink",
    headerSubtitle: "text-muted",
    formButtonPrimary: "bg-accent shadow-none hover:bg-accent-hover",
    footerActionLink: "font-semibold text-accent-strong hover:text-accent-strong",
    formFieldInput: "border-accent-border bg-white focus:border-accent focus:ring-focus",
    socialButtonsBlockButton: "border-accent-border bg-white text-ink-secondary hover:bg-canvas",
    dividerLine: "bg-border",
    dividerText: "text-subtle",
  },
};

export function AuthShell({ children }: { children: ReactNode }) {
  return <main className="grain min-h-[100dvh] bg-surface-muted px-5 py-[max(2rem,env(safe-area-inset-top))] text-ink">
    <div className="mx-auto flex min-h-[calc(100dvh-4rem)] w-full max-w-md flex-col items-center justify-center">
      <div className="mb-7 flex flex-col items-center text-center">
        <span className="block size-16 overflow-hidden rounded-[20px] border border-accent-border bg-white shadow-[0_18px_46px_-28px_rgb(var(--shadow)/.65)]">
          <Image className="size-full scale-[1.08] object-cover" src="/gharsaathi-icon.png" alt="" width={72} height={72} priority />
        </span>
        <p className="font-display mt-4 text-[26px] font-semibold tracking-[-.045em]">GharSaathi</p>
      </div>
      {children}
    </div>
  </main>;
}
