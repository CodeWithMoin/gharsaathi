import { ClerkProvider } from "@clerk/nextjs";
import Script from "next/script";
import type { Metadata } from "next";
import "./globals.css";
import { PrivateDeploymentGate } from "./private-deployment-gate";

export const metadata: Metadata = {
  title: "GharSaathi · Your AI household shopping agent",
  description: "Speak naturally to search products across connected quick-commerce stores in English, Hindi, or Hinglish.",
  // The manifest lets the app be added to the Home Screen; iOS delivers web push only there.
  manifest: "/manifest.webmanifest",
  // Setting `icons` replaces the file-based app/icon.svg favicon, so list it explicitly.
  icons: { icon: [{ url: "/icon.svg", type: "image/svg+xml" }], apple: "/icons/gharsaathi-180.png" },
  appleWebApp: { capable: true, title: "GharSaathi", statusBarStyle: "default" },
};

const STRIP_EXTENSION_ATTRIBUTES = `(function(){var A=["bis_skin_checked","bis_register","bis_use"];function clean(el){if(!el||!el.removeAttribute)return;for(var i=0;i<A.length;i++)if(el.hasAttribute&&el.hasAttribute(A[i]))el.removeAttribute(A[i]);var n=el.attributes;if(n)for(var j=n.length-1;j>=0;j--){var k=n[j].name;if(k.indexOf("__processed_")===0)el.removeAttribute(k);}}function all(){var e=document.querySelectorAll("[bis_skin_checked],[bis_register],[bis_use]");for(var i=0;i<e.length;i++)clean(e[i]);clean(document.body);}all();new MutationObserver(function(m){for(var i=0;i<m.length;i++){if(m[i].type==="attributes")clean(m[i].target);}}).observe(document.documentElement,{attributes:true,subtree:true,attributeFilter:A});})();`;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  // Browser extensions (Grammarly, password managers, translators) add attributes to
  // <html>/<body> before React hydrates; ignore those attribute-only mismatches.
  return <html lang="en-IN" suppressHydrationWarning><body suppressHydrationWarning>
    {/* Some extensions stamp bis_* attributes on every element before hydration; strip them so React sees our markup. */}
    <Script id="strip-extension-attributes" strategy="beforeInteractive">{STRIP_EXTENSION_ATTRIBUTES}</Script>
    <ClerkProvider><PrivateDeploymentGate>{children}</PrivateDeploymentGate></ClerkProvider></body></html>;
}
