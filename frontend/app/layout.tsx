import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "GharCart · Instamart search",
  description: "Search Swiggy Instamart for household essentials.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en-IN"><body>{children}</body></html>;
}
