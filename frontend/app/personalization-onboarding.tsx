"use client";

import { Check, ClockCounterClockwise, ShieldCheck } from "@phosphor-icons/react";
import { usePurchaseHistoryImport } from "./purchase-history-import";

type Props = {
  commerceMode: "demo" | "live";
  swiggyConnected: boolean;
  zeptoConnected: boolean;
};

export function PersonalizationOnboarding({ commerceMode, swiggyConnected, zeptoConnected }: Props) {
  const { permission, retentionDays, setRetentionDays, busy, result, error, enable, skip, refresh, disable } = usePurchaseHistoryImport({ swiggyConnected, zeptoConnected });

  if (permission === undefined) {
    return <div className="mt-7 h-32 animate-pulse rounded-2xl bg-[#eee2d8]" aria-label="Loading personalization settings" />;
  }

  if (permission?.allowed) {
    return (
      <section className="mt-7 border-t border-[#eaded5] pt-6" aria-label="Order history personalization">
        <div className="rounded-2xl border border-[#cfe2d2] bg-[#f3faf4] p-4">
          <div className="flex items-start gap-3">
            <span className="grid size-8 shrink-0 place-items-center rounded-full bg-[#dcefe0] text-[#397144]"><Check size={17} weight="bold" /></span>
            <div><h3 className="font-semibold text-[#2f6339]">Your usuals are on</h3><p className="mt-1 text-xs leading-5 text-[#55745c]">Recent orders from your connected stores can suggest repeat items. Nothing is added or ordered automatically.</p></div>
          </div>
          {result && <p className="mt-3 rounded-xl bg-white/70 px-3 py-2 text-[11px] leading-5 text-[#55745c]">{result.cached ? "Already checked recently—no provider call was repeated." : `Checked ${result.providers} store${result.providers === 1 ? "" : "s"}; added ${result.imported_orders} new orders and ${result.imported_occurrences} item occurrences.`}</p>}
          <div className="mt-4 flex items-center gap-4">
            <button type="button" onClick={() => void refresh()} disabled={busy !== null || (!swiggyConnected && !zeptoConnected) || commerceMode === "demo"} className="flex items-center gap-1.5 text-xs font-semibold text-[#397144] disabled:opacity-45"><ClockCounterClockwise size={15} /> {busy === "refresh" ? "Refreshing…" : "Refresh usuals"}</button>
            <button type="button" onClick={() => void disable()} disabled={busy !== null} className="text-xs font-semibold text-[#9e4536] disabled:opacity-45">{busy === "disable" ? "Removing…" : "Turn off & delete"}</button>
          </div>
        </div>
        {error && <p className="mt-3 text-xs leading-5 text-[#a33d2c]">{error}</p>}
      </section>
    );
  }

  return (
    <section className="mt-7 border-t border-[#eaded5] pt-6" aria-label="Personalize from past orders">
      <p className="text-[10px] font-semibold tracking-[.16em] text-[#c6531d]">OPTIONAL PERSONALIZATION</p>
      <h3 className="font-display mt-2 text-xl font-semibold">Want me to learn your usuals?</h3>
      <p className="mt-2 text-xs leading-5 text-[#806f65]">With your approval, GharSaathi reads recent orders from every connected store to find repeat patterns such as your usual milk or atta. Purchase history never proves what is currently in stock at home.</p>

      <label className="mt-4 block text-[11px] font-semibold text-[#5f4a3f]" htmlFor="history-retention">Delete imported pattern data after</label>
      <select id="history-retention" value={retentionDays} onChange={(event) => setRetentionDays(Number(event.target.value))} className="mt-2 w-full rounded-xl border border-[#deccbf] bg-white px-3 py-2.5 text-sm outline-none focus:border-[#c6531d]">
        <option value={30}>30 days</option>
        <option value={90}>90 days</option>
        <option value={365}>1 year</option>
      </select>

      <div className="mt-4 flex items-start gap-2 rounded-xl bg-[#f7ede5] px-3 py-3 text-[11px] leading-5 text-[#806f65]"><ShieldCheck size={16} className="mt-0.5 shrink-0" /><span>Read-only and server-side. Not sent to Jev. You can turn it off and delete it anytime.</span></div>
      {commerceMode === "demo" && <p className="mt-3 text-xs leading-5 text-[#806f65]">History import is disabled in safe demo mode.</p>}
      {commerceMode === "live" && !swiggyConnected && !zeptoConnected && <p className="mt-3 text-xs leading-5 text-[#806f65]">Connect at least one store first. Swiggy uses one history read; Zepto safely checks at most three delivered-order details, sequentially.</p>}
      {error && <p className="mt-3 text-xs leading-5 text-[#a33d2c]">{error}</p>}

      <div className="mt-4 grid grid-cols-2 gap-2">
        <button type="button" onClick={() => void skip()} disabled={busy !== null} className="rounded-full border border-[#deccbf] bg-white px-4 py-3 text-sm font-semibold text-[#6c5548] disabled:opacity-45">{busy === "skip" ? "Saving…" : permission ? "Keep off" : "Not now"}</button>
        <button type="button" onClick={() => void enable()} disabled={busy !== null || commerceMode === "demo" || (!swiggyConnected && !zeptoConnected)} className="rounded-full bg-[#c6531d] px-4 py-3 text-sm font-semibold text-white disabled:opacity-40">{busy === "enable" ? "Importing…" : `Use recent orders`}</button>
      </div>
    </section>
  );
}
