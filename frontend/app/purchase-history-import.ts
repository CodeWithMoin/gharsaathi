"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useApiRequest } from "./authenticated-api";
import { importSummaryLine } from "./history-import-summary.mjs";

export type HistoryProvider = "swiggy" | "zepto";

export type HistoryPermission = {
  data_category: string;
  purpose: string;
  allowed: boolean;
  retention_days: number | null;
};

export type HistoryCoverage = "last_15_days" | "full_history";

export type HistoryImportResult = {
  imported_orders: number;
  imported_products: number;
  imported_occurrences: number;
  pruned_orders: number;
  cached: boolean;
  provider?: HistoryProvider | null;
  full?: boolean;
  coverage?: HistoryCoverage | null;
  fetched_orders?: number;
  skipped_known?: number;
  detail_failures?: number;
  stopped_reason?: string | null;
  complete?: boolean;
  window_start?: string | null;
  window_end?: string | null;
};

/** Server-side sync state per store; reading it never calls the store. */
export type HistorySyncStatus = {
  provider: HistoryProvider;
  coverage: HistoryCoverage;
  last_success_at: string | null;
  window_start: string | null;
  window_end: string | null;
  complete: boolean;
  last_error_code: string | null;
  rate_limited_until: string | null;
  full_resync_available_at: string | null;
  auto_sync_enabled: boolean;
  next_auto_sync_at: string | null;
};

export type HistoryImportSummary = HistoryImportResult & { providers: number; cachedProviders: number; lines: string[] };

export type ProviderImportProgress =
  | { state: "waiting" }
  | { state: "running" }
  | { state: "done"; result: HistoryImportResult }
  | { state: "failed"; message: string };

export type HistoryBusy = "enable" | "skip" | "refresh" | "full" | "auto" | "disable" | null;

export const RETENTION_CHOICES = [
  { days: 30, label: "30 din" },
  { days: 90, label: "90 din" },
  { days: 365, label: "1 saal" },
] as const;

export const providerLabel = (provider: HistoryProvider) => provider === "swiggy" ? "Swiggy Instamart" : "Zepto";

/**
 * Purchase-history permission and import, shared by the setup sheet and onboarding.
 * Imports run one store at a time so Zepto's sequential detail reads stay bounded.
 */
export function usePurchaseHistoryImport({ swiggyConnected, zeptoConnected }: { swiggyConnected: boolean; zeptoConnected: boolean }) {
  const request = useApiRequest();
  const [permission, setPermission] = useState<HistoryPermission | null | undefined>(undefined);
  const [loadError, setLoadError] = useState("");
  const [retentionDays, setRetentionDays] = useState(90);
  const [busy, setBusy] = useState<HistoryBusy>(null);
  const [result, setResult] = useState<HistoryImportSummary | null>(null);
  const [progress, setProgress] = useState<Partial<Record<HistoryProvider, ProviderImportProgress>>>({});
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const [statuses, setStatuses] = useState<Partial<Record<HistoryProvider, HistorySyncStatus>>>({});

  const loadStatuses = useCallback(async () => {
    try {
      const rows = await request<HistorySyncStatus[]>("/api/memory/purchase-history/sync-state");
      setStatuses(Object.fromEntries(rows.map((row) => [row.provider, row])));
    } catch {
      // Status is informational; the import itself reports its own errors.
    }
  }, [request]);

  useEffect(() => { void loadStatuses(); }, [loadStatuses, reloadKey]);

  useEffect(() => {
    let active = true;
    setPermission(undefined);
    setLoadError("");
    void request<HistoryPermission[]>("/api/memory/permissions")
      .then((permissions) => {
        if (!active) return;
        const current = permissions.find((item) => item.data_category === "purchase_history" && item.purpose === "proactive_reorder") ?? null;
        setPermission(current);
        if (current?.retention_days) setRetentionDays(current.retention_days);
      })
      .catch((cause: unknown) => {
        if (!active) return;
        setPermission(null);
        setLoadError(cause instanceof Error ? cause.message : "Could not load personalization settings.");
      });
    return () => { active = false; };
  }, [request, reloadKey]);

  const savePermission = useCallback((allowed: boolean, days: number | null) => request<HistoryPermission>("/api/memory/permissions/purchase_history/proactive_reorder", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      allowed,
      granularity: allowed ? "recent_order_items_and_purchase_timing" : "disabled",
      retention_days: days,
      sharing_scope: "agent_and_user",
      user_control: "view_edit_delete",
    }),
  }), [request]);

  const syncConnectedProviders = useCallback(async (full = false): Promise<HistoryImportSummary> => {
    const providers = ([swiggyConnected ? "swiggy" : null, zeptoConnected ? "zepto" : null] as const).filter((provider): provider is HistoryProvider => provider !== null);
    if (!providers.length) throw new Error("Connect at least one store before importing order history.");
    setProgress(Object.fromEntries(providers.map((provider) => [provider, { state: "waiting" }])));
    const totals: HistoryImportSummary = { imported_orders: 0, imported_products: 0, imported_occurrences: 0, pruned_orders: 0, cached: false, providers: 0, cachedProviders: 0, lines: [] };
    const failures: string[] = [];
    for (const provider of providers) {
      setProgress((current) => ({ ...current, [provider]: { state: "running" } }));
      try {
        const imported = await request<HistoryImportResult>(`/api/memory/purchase-history/sync?provider=${provider}${full ? "&full=true" : ""}`, { method: "POST" });
        totals.imported_orders += imported.imported_orders;
        totals.imported_products += imported.imported_products;
        totals.imported_occurrences += imported.imported_occurrences;
        totals.pruned_orders += imported.pruned_orders;
        totals.providers += 1;
        totals.cachedProviders += Number(imported.cached);
        totals.lines.push(importSummaryLine(provider, imported));
        setProgress((current) => ({ ...current, [provider]: { state: "done", result: imported } }));
      } catch (cause) {
        const message = cause instanceof Error ? cause.message : "history unavailable";
        failures.push(`${provider === "swiggy" ? "Swiggy" : "Zepto"}: ${message}`);
        setProgress((current) => ({ ...current, [provider]: { state: "failed", message } }));
      }
    }
    totals.cached = totals.providers > 0 && totals.cachedProviders === totals.providers;
    void loadStatuses();
    if (!totals.providers) throw new Error(failures.join(" "));
    if (failures.length) setError(`One store could not be updated. ${failures.join(" ")}`);
    return totals;
  }, [loadStatuses, request, swiggyConnected, zeptoConnected]);

  const run = useCallback(async (kind: Exclude<HistoryBusy, null>, action: () => Promise<void>, fallback: string) => {
    setBusy(kind);
    setError("");
    try {
      await action();
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : fallback);
      return false;
    } finally {
      setBusy(null);
    }
  }, []);

  const enable = useCallback(() => run("enable", async () => {
    setPermission(await savePermission(true, retentionDays));
    setResult(await syncConnectedProviders());
  }, "Could not import your recent orders."), [retentionDays, run, savePermission, syncConnectedProviders]);

  const skip = useCallback(() => run("skip", async () => {
    setPermission(await savePermission(false, 0));
    setResult(null);
  }, "Could not save your choice."), [run, savePermission]);

  const refresh = useCallback(() => run("refresh", async () => {
    setResult(await syncConnectedProviders());
  }, "Could not refresh your recent orders."), [run, syncConnectedProviders]);

  const fullResync = useCallback(() => run("full", async () => {
    setResult(await syncConnectedProviders(true));
  }, "Could not re-import your order history."), [run, syncConnectedProviders]);

  const setAutoSync = useCallback((provider: HistoryProvider, enabled: boolean) => run("auto", async () => {
    const status = await request<HistorySyncStatus>("/api/memory/purchase-history/auto-sync", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, enabled }),
    });
    setStatuses((current) => ({ ...current, [provider]: status }));
  }, "Could not save the weekly-sync setting."), [request, run]);

  const disable = useCallback(() => run("disable", async () => {
    setPermission(await savePermission(false, 0));
    await request<null>("/api/memory/purchase-history", { method: "DELETE" });
    setResult(null);
    setProgress({});
    setStatuses({});
  }, "Could not remove personalization data."), [request, run, savePermission]);

  return {
    permission,
    loadError,
    reload: () => setReloadKey((value) => value + 1),
    retentionDays,
    setRetentionDays,
    busy,
    result,
    progress,
    error: error || loadError,
    enable,
    skip,
    refresh,
    fullResync,
    statuses,
    setAutoSync,
    disable,
  };
}

/**
 * Opt-in weekly sync, run lazily once per app open (never on a timer). The
 * server decides whether a sync is due (at most once a week per store, skipped
 * during a recorded rate-limit cooldown); this only asks.
 */
export function useWeeklyHistoryAutoSync({ enabled, swiggyConnected, zeptoConnected }: { enabled: boolean; swiggyConnected: boolean; zeptoConnected: boolean }) {
  const request = useApiRequest();
  const asked = useRef<Set<HistoryProvider>>(new Set());

  useEffect(() => {
    if (!enabled) return;
    const providers = ([swiggyConnected ? "swiggy" : null, zeptoConnected ? "zepto" : null] as const)
      .filter((provider): provider is HistoryProvider => provider !== null && !asked.current.has(provider));
    if (!providers.length) return;
    providers.forEach((provider) => asked.current.add(provider));
    void (async () => {
      for (const provider of providers) {
        try {
          await request(`/api/memory/purchase-history/auto-sync/run?provider=${provider}`, { method: "POST" });
        } catch {
          // Best effort; the explicit import in Settings still works.
        }
      }
    })();
  }, [enabled, request, swiggyConnected, zeptoConnected]);
}
