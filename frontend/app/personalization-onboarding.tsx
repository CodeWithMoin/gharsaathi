"use client";

import { Check, ClockCounterClockwise, ShieldCheck } from "@phosphor-icons/react";
import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const DEV_USER_ID = process.env.NEXT_PUBLIC_DEV_USER_ID ?? "local-test-user";

type Permission = {
  data_category: string;
  purpose: string;
  allowed: boolean;
  retention_days: number | null;
};

type ImportResult = {
  imported_orders: number;
  imported_products: number;
  imported_occurrences: number;
  pruned_orders: number;
};

type Props = {
  commerceMode: "demo" | "live";
  swiggyConnected: boolean;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("X-User-Id", DEV_USER_ID);
  const response = await fetch(`${API}${path}`, { cache: "no-store", ...init, headers });
  const payload = response.status === 204 ? null : await response.json();
  if (!response.ok) {
    throw new Error(payload && typeof payload.detail === "string" ? payload.detail : "Could not save personalization settings.");
  }
  return payload as T;
}

export function PersonalizationOnboarding({ commerceMode, swiggyConnected }: Props) {
  const [permission, setPermission] = useState<Permission | null | undefined>(undefined);
  const [retentionDays, setRetentionDays] = useState(90);
  const [busy, setBusy] = useState<"enable" | "skip" | "refresh" | "disable" | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void request<Permission[]>("/api/memory/permissions")
      .then((permissions) => {
        if (!active) return;
        const current = permissions.find((item) => item.data_category === "purchase_history" && item.purpose === "proactive_reorder") ?? null;
        setPermission(current);
        if (current?.retention_days) setRetentionDays(current.retention_days);
      })
      .catch((cause: unknown) => {
        if (!active) return;
        setPermission(null);
        setError(cause instanceof Error ? cause.message : "Could not load personalization settings.");
      });
    return () => { active = false; };
  }, []);

  async function savePermission(allowed: boolean, days: number | null) {
    return request<Permission>("/api/memory/permissions/purchase_history/proactive_reorder", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        allowed,
        granularity: allowed ? "recent_order_items_and_purchase_timing" : "disabled",
        retention_days: days,
        sharing_scope: "agent_and_user",
        user_control: "view_edit_delete",
      }),
    });
  }

  async function enable() {
    setBusy("enable");
    setError("");
    try {
      const saved = await savePermission(true, retentionDays);
      setPermission(saved);
      const imported = await request<ImportResult>("/api/memory/purchase-history/sync", { method: "POST" });
      setResult(imported);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not import your recent orders.");
    } finally {
      setBusy(null);
    }
  }

  async function skip() {
    setBusy("skip");
    setError("");
    try {
      setPermission(await savePermission(false, 0));
      setResult(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save your choice.");
    } finally {
      setBusy(null);
    }
  }

  async function refresh() {
    setBusy("refresh");
    setError("");
    try {
      setResult(await request<ImportResult>("/api/memory/purchase-history/sync", { method: "POST" }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not refresh your recent orders.");
    } finally {
      setBusy(null);
    }
  }

  async function disable() {
    setBusy("disable");
    setError("");
    try {
      const revoked = await savePermission(false, 0);
      setPermission(revoked);
      await request<null>("/api/memory/purchase-history", { method: "DELETE" });
      setResult(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not remove personalization data.");
    } finally {
      setBusy(null);
    }
  }

  if (permission === undefined) {
    return <div className="mt-7 h-32 animate-pulse rounded-2xl bg-[#eee2d8]" aria-label="Loading personalization settings" />;
  }

  if (permission?.allowed) {
    return (
      <section className="mt-7 border-t border-[#eaded5] pt-6" aria-label="Order history personalization">
        <div className="rounded-2xl border border-[#cfe2d2] bg-[#f3faf4] p-4">
          <div className="flex items-start gap-3">
            <span className="grid size-8 shrink-0 place-items-center rounded-full bg-[#dcefe0] text-[#397144]"><Check size={17} weight="bold" /></span>
            <div><h3 className="font-semibold text-[#2f6339]">Your usuals are on</h3><p className="mt-1 text-xs leading-5 text-[#55745c]">Recent Swiggy orders can suggest repeat items. Nothing is added or ordered automatically.</p></div>
          </div>
          {result && <p className="mt-3 rounded-xl bg-white/70 px-3 py-2 text-[11px] leading-5 text-[#55745c]">Updated from {result.imported_orders} new orders and {result.imported_occurrences} item occurrences.</p>}
          <div className="mt-4 flex items-center gap-4">
            <button type="button" onClick={() => void refresh()} disabled={busy !== null || !swiggyConnected || commerceMode === "demo"} className="flex items-center gap-1.5 text-xs font-semibold text-[#397144] disabled:opacity-45"><ClockCounterClockwise size={15} /> {busy === "refresh" ? "Refreshing…" : "Refresh usuals"}</button>
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
      <p className="mt-2 text-xs leading-5 text-[#806f65]">With your approval, GharSaathi reads up to 15 recent Swiggy orders to find repeat patterns such as your usual milk or atta. Purchase history never proves what is currently in stock at home.</p>

      <label className="mt-4 block text-[11px] font-semibold text-[#5f4a3f]" htmlFor="history-retention">Delete imported pattern data after</label>
      <select id="history-retention" value={retentionDays} onChange={(event) => setRetentionDays(Number(event.target.value))} className="mt-2 w-full rounded-xl border border-[#deccbf] bg-white px-3 py-2.5 text-sm outline-none focus:border-[#c6531d]">
        <option value={30}>30 days</option>
        <option value={90}>90 days</option>
        <option value={365}>1 year</option>
      </select>

      <div className="mt-4 flex items-start gap-2 rounded-xl bg-[#f7ede5] px-3 py-3 text-[11px] leading-5 text-[#806f65]"><ShieldCheck size={16} className="mt-0.5 shrink-0" /><span>Read-only and server-side. Not sent to Jev. You can turn it off and delete it anytime.</span></div>
      {commerceMode === "demo" && <p className="mt-3 text-xs leading-5 text-[#806f65]">History import is disabled in safe demo mode.</p>}
      {commerceMode === "live" && !swiggyConnected && <p className="mt-3 text-xs leading-5 text-[#806f65]">Connect Swiggy first to import timestamped order history safely. Zepto remains connected for shopping, but its history list does not currently include the timing needed for repeat-pattern learning.</p>}
      {error && <p className="mt-3 text-xs leading-5 text-[#a33d2c]">{error}</p>}

      <div className="mt-4 grid grid-cols-2 gap-2">
        <button type="button" onClick={() => void skip()} disabled={busy !== null} className="rounded-full border border-[#deccbf] bg-white px-4 py-3 text-sm font-semibold text-[#6c5548] disabled:opacity-45">{busy === "skip" ? "Saving…" : permission ? "Keep off" : "Not now"}</button>
        <button type="button" onClick={() => void enable()} disabled={busy !== null || commerceMode === "demo" || !swiggyConnected} className="rounded-full bg-[#c6531d] px-4 py-3 text-sm font-semibold text-white disabled:opacity-40">{busy === "enable" ? "Importing…" : `Use recent orders`}</button>
      </div>
    </section>
  );
}
