"use client";

import { useClerk } from "@clerk/nextjs";
import { ReactNode, useEffect, useState } from "react";
import { AuthShell } from "./auth-shell";
import { PRIVATE_DEPLOYMENT_EVENT, PRIVATE_DEPLOYMENT_MESSAGE } from "./private-deployment.mjs";

/** Replaces the app with a plain notice once the backend refuses this account as not the deployment's owner. */
export function PrivateDeploymentGate({ children }: { children: ReactNode }) {
  const { signOut } = useClerk();
  const [refused, setRefused] = useState(false);

  useEffect(() => {
    const refuse = () => setRefused(true);
    window.addEventListener(PRIVATE_DEPLOYMENT_EVENT, refuse);
    return () => window.removeEventListener(PRIVATE_DEPLOYMENT_EVENT, refuse);
  }, []);

  if (!refused) return <>{children}</>;
  return <AuthShell>
    <div role="alert" className="w-full rounded-[28px] border border-border-strong bg-surface p-6 text-center">
      <p className="font-display text-xl font-semibold tracking-[-.03em] text-ink">{PRIVATE_DEPLOYMENT_MESSAGE}</p>
      <p className="mt-2 text-sm text-muted">This account does not have access.</p>
      <button type="button" onClick={() => void signOut({ redirectUrl: "/sign-in" })} className="mt-5 flex min-h-12 w-full items-center justify-center rounded-2xl border border-accent-border bg-surface px-4 text-sm font-semibold text-ink-secondary transition hover:bg-canvas focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus">
        Sign out
      </button>
    </div>
  </AuthShell>;
}
