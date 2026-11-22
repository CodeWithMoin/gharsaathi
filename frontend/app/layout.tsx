import { ClerkProvider } from "@clerk/nextjs";
import type { Metadata } from "next";
import "./globals.css";
import { PrivateDeploymentGate } from "./private-deployment-gate";

export const metadata: Metadata = {
  title: "GharSaathi · Your AI household shopping agent",
  description: "Speak naturally to search products across connected quick-commerce stores in English, Hindi, or Hinglish.",
  // The manifest lets the app be added to the Home Screen; iOS delivers web push only there.
  manifest: "/manifest.webmanifest",
  icons: { apple: "/icons/gharsaathi-180.png" },
  appleWebApp: { capable: true, title: "GharSaathi", statusBarStyle: "default" },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  // Browser extensions (Grammarly, password managers, translators) add attributes to
  // <html>/<body> before React hydrates; ignore those attribute-only mismatches.
  return <html lang="en-IN" suppressHydrationWarning><body suppressHydrationWarning><ClerkProvider><PrivateDeploymentGate>{children}</PrivateDeploymentGate></ClerkProvider></body></html>;
}
