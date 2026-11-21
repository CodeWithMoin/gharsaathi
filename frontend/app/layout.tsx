import { ClerkProvider } from "@clerk/nextjs";
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "GharSaathi · Your AI household shopping agent",
  description: "Speak naturally to search products across connected quick-commerce stores in English, Hindi, or Hinglish.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en-IN"><body><ClerkProvider>{children}</ClerkProvider></body></html>;
}
