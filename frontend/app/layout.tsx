import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "GharSaathi · Your AI household shopping agent",
  description: "Speak naturally to search for household essentials in English, Hindi, or Hinglish.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en-IN"><body>{children}</body></html>;
}
