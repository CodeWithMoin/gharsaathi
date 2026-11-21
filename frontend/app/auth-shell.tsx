import Image from "next/image";
import { ReactNode } from "react";

export const clerkAppearance = {
  variables: {
    colorPrimary: "#c6531d",
    colorText: "#35241c",
    colorTextSecondary: "#806f65",
    colorBackground: "#fffdf9",
    borderRadius: "1.25rem",
    fontFamily: '"DM Sans", sans-serif',
  },
  elements: {
    rootBox: "w-full",
    cardBox: "w-full shadow-none",
    card: "w-full rounded-[28px] border border-[#e7d6ca] bg-[#fffdf9] shadow-[0_28px_70px_-48px_rgba(101,52,27,.65)]",
    headerTitle: "font-display tracking-[-.035em] text-[#35241c]",
    headerSubtitle: "text-[#806f65]",
    formButtonPrimary: "bg-[#c6531d] shadow-none hover:bg-[#ad4517]",
    footerActionLink: "font-semibold text-[#a94318] hover:text-[#833412]",
    formFieldInput: "border-[#ddc8ba] bg-white focus:border-[#c6531d] focus:ring-[#c6531d]",
    socialButtonsBlockButton: "border-[#ddc8ba] bg-white text-[#4d392f] hover:bg-[#fff8f2]",
    dividerLine: "bg-[#eadbd0]",
    dividerText: "text-[#9a8579]",
  },
};

export function AuthShell({ children }: { children: ReactNode }) {
  return <main className="grain min-h-[100dvh] bg-[#f8f2ea] px-5 py-[max(2rem,env(safe-area-inset-top))] text-[#35241c]">
    <div className="mx-auto flex min-h-[calc(100dvh-4rem)] w-full max-w-md flex-col items-center justify-center">
      <div className="mb-7 flex flex-col items-center text-center">
        <span className="block size-16 overflow-hidden rounded-[20px] border border-[#e7cbb8] bg-white shadow-[0_18px_46px_-28px_rgba(101,52,27,.65)]">
          <Image className="size-full scale-[1.08] object-cover" src="/gharsaathi-icon.png" alt="" width={72} height={72} priority />
        </span>
        <p className="font-display mt-4 text-[26px] font-semibold tracking-[-.045em]">GharSaathi</p>
      </div>
      {children}
    </div>
  </main>;
}
