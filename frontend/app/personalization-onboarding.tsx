"use client";

import { Check, ClockCounterClockwise, ShieldCheck } from "@phosphor-icons/react";
import { fullResyncAvailability } from "./history-import-summary.mjs";
import { providerLabel, usePurchaseHistoryImport, type HistoryProvider } from "./purchase-history-import";

type Props = {
  commerceMode: "demo" | "live";
  swiggyConnected: boolean;
  zeptoConnected: boolean;
};

export function PersonalizationOnboarding({ commerceMode, swiggyConnected, zeptoConnected }: Props) {
  const { permission, retentionDays, setRetentionDays, busy, result, error, enable, skip, refresh, fullResync, statuses, setAutoSync, disable } = usePurchaseHistoryImport({ swiggyConnected, zeptoConnected });
  const connectedProviders = ([swiggyConnected ? "swiggy" : null, zeptoConnected ? "zepto" : null] as const).filter((provider): provider is HistoryProvider => provider !== null);
  const fullBlocked = connectedProviders.map((provider) => ({ provider, ...fullResyncAvailability(statuses[provider]) })).filter((item) => !item.available);
  const fullAvailable = connectedProviders.length > 0 && fullBlocked.length < connectedProviders.length;

  if (permission === undefined) {
    return <div className="mt-7 h-32 animate-pulse rounded-2xl bg-surface-strong" aria-label="Loading personalization settings" />;
  }

  if (permission?.allowed) {
    return (
      <section className="mt-7 border-t border-border pt-6" aria-label="Order history personalization">
        <div className="rounded-2xl border border-success-border bg-success-soft p-4">
          <div className="flex items-start gap-3">
            <span className="grid size-8 shrink-0 place-items-center rounded-full bg-success-border text-success"><Check size={17} weight="bold" /></span>
            <div><h3 className="font-semibold text-success-strong">Your usuals are on</h3><p className="mt-1 text-xs leading-5 text-success">Recent orders from your connected stores can suggest repeat items. Nothing is added or ordered automatically.</p></div>
          </div>
          {result && <div className="mt-3 space-y-1 rounded-xl bg-white/70 px-3 py-2 text-[11px] leading-5 text-success">{result.cached ? <p>Already checked recently—no provider call was repeated.</p> : result.lines.map((line) => <p key={line}>{line}</p>)}</div>}
          <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2">
            <button type="button" onClick={() => void refresh()} disabled={busy !== null || !connectedProviders.length || commerceMode === "demo"} className="flex items-center gap-1.5 text-xs font-semibold text-success disabled:opacity-45"><ClockCounterClockwise size={15} /> {busy === "refresh" ? "Refreshing…" : "Refresh usuals"}</button>
            <button type="button" onClick={() => void fullResync()} disabled={busy !== null || !fullAvailable || commerceMode === "demo"} className="text-xs font-semibold text-success disabled:opacity-45">{busy === "full" ? "Re-importing…" : "Re-import full history"}</button>
            <button type="button" onClick={() => void disable()} disabled={busy !== null} className="text-xs font-semibold text-danger-hover disabled:opacity-45">{busy === "disable" ? "Removing…" : "Turn off & delete"}</button>
          </div>
          {fullBlocked.length > 0 && <p className="mt-2 text-[11px] leading-5 text-success">{fullBlocked.map((item) => `${providerLabel(item.provider)} full re-import: ${item.label}.`).join(" ")}</p>}
          {commerceMode === "live" && connectedProviders.length > 0 && <div className="mt-4 space-y-2 border-t border-success-border pt-3">
            {connectedProviders.map((provider) => {
              const checked = statuses[provider]?.auto_sync_enabled === true;
              return <label key={provider} className="flex items-start gap-3 text-xs leading-5 text-success-strong">
                <input type="checkbox" role="switch" aria-checked={checked} checked={checked} disabled={busy !== null} onChange={(event) => void setAutoSync(provider, event.target.checked)} className="mt-1 size-4 accent-current" />
                <span><span className="font-semibold">Keep my {provider === "swiggy" ? "Swiggy" : "Zepto"} history up to date</span><span className="block text-[11px] text-success">{provider === "swiggy" ? "Swiggy only shares the last 15 days, so" : "When on,"} GharSaathi reads new orders at most once a week, when you open the app. Read-only.</span></span>
              </label>;
            })}
          </div>}
        </div>
        {error && <p className="mt-3 text-xs leading-5 text-danger">{error}</p>}
      </section>
    );
  }

  return (
    <section className="mt-7 border-t border-border pt-6" aria-label="Personalize from past orders">
      <p className="text-[10px] font-semibold tracking-[.16em] text-accent-ink">OPTIONAL PERSONALIZATION</p>
      <h3 className="font-display mt-2 text-xl font-semibold">Want me to learn your usuals?</h3>
      <p className="mt-2 text-xs leading-5 text-muted">With your approval, GharSaathi reads recent orders from every connected store to find repeat patterns such as your usual milk or atta. Purchase history never proves what is currently in stock at home.</p>

      <label className="mt-4 block text-[11px] font-semibold text-ink-secondary" htmlFor="history-retention">Delete imported pattern data after</label>
      <select id="history-retention" value={retentionDays} onChange={(event) => setRetentionDays(Number(event.target.value))} className="mt-2 w-full rounded-xl border border-border-strong bg-white px-3 py-2.5 text-sm outline-none focus:border-accent">
        <option value={30}>30 days</option>
        <option value={90}>90 days</option>
        <option value={365}>1 year</option>
      </select>

      <div className="mt-4 flex items-start gap-2 rounded-xl bg-surface-muted px-3 py-3 text-[11px] leading-5 text-muted"><ShieldCheck size={16} className="mt-0.5 shrink-0" /><span>Read-only and server-side. Not sent to Jev. You can turn it off and delete it anytime.</span></div>
      {commerceMode === "demo" && <p className="mt-3 text-xs leading-5 text-muted">History import is disabled in safe demo mode.</p>}
      {commerceMode === "live" && !swiggyConnected && !zeptoConnected && <p className="mt-3 text-xs leading-5 text-muted">Connect at least one store first. Swiggy shares only the last 15 days of orders; Zepto is read slowly, one order at a time, and a long history finishes over a few refreshes.</p>}
      {error && <p className="mt-3 text-xs leading-5 text-danger">{error}</p>}

      <div className="mt-4 grid grid-cols-2 gap-2">
        <button type="button" onClick={() => void skip()} disabled={busy !== null} className="rounded-full border border-border-strong bg-white px-4 py-3 text-sm font-semibold text-ink-secondary disabled:opacity-45">{busy === "skip" ? "Saving…" : permission ? "Keep off" : "Not now"}</button>
        <button type="button" onClick={() => void enable()} disabled={busy !== null || commerceMode === "demo" || (!swiggyConnected && !zeptoConnected)} className="rounded-full bg-accent px-4 py-3 text-sm font-semibold text-on-accent disabled:opacity-40">{busy === "enable" ? "Importing…" : `Use recent orders`}</button>
      </div>
    </section>
  );
}
