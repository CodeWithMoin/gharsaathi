"use client";

import { useMemo } from "react";
import { ApiError, useApiRequest } from "./authenticated-api";
import type { CorrectionPayload, MemoryRecord, MemorySummaryResponse, PermissionLike } from "./memory-groups.mjs";

export const PREFERENCE_PURPOSE = "personalize_search";
/** Mirrors backend `app.memory.recall.PREFERENCE_TYPES`. */
export const PREFERENCE_CATEGORIES = ["brand_preference", "pack_size_preference", "quantity_preference", "product_avoidance"] as const;

export type MemoryPermission = PermissionLike & { id?: string; granularity?: string | null; retention_days?: number | null };

/**
 * Provisional contract for routes the memory-write-manage backend may add.
 * Older servers answer FastAPI's generic 404 "Not Found"; callers then hide
 * the control instead of failing.
 */
export const MEMORY_ROUTES = {
  undo: (id: string) => `/api/memory/${encodeURIComponent(id)}/undo`,
  forgetAllToken: "/api/memory/forget-all/token",
  forgetAll: "/api/memory/forget-all",
} as const;

export class RouteUnavailableError extends Error {
  constructor() {
    super("This action is not available on the server yet.");
    this.name = "RouteUnavailableError";
  }
}

/** A missing route (not a missing memory) comes back as FastAPI's bare "Not Found" or 405. */
export function isMissingRoute(error: unknown) {
  return error instanceof ApiError && (error.status === 405 || (error.status === 404 && error.message === "Not Found"));
}

async function optionalRoute<T>(call: () => Promise<T>): Promise<T> {
  try {
    return await call();
  } catch (error) {
    if (isMissingRoute(error)) throw new RouteUnavailableError();
    throw error;
  }
}

const json = (body: unknown): RequestInit => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export function useMemoryApi() {
  const request = useApiRequest();
  return useMemo(() => ({
    list: () => request<MemoryRecord[]>("/api/memory"),
    summary: () => request<MemorySummaryResponse>("/api/memory/summary"),
    clearOrderHistory: () => request<null>("/api/memory/purchase-history", { method: "DELETE" }),
    updateProfile: (changes: Record<string, string | null>) => request<unknown>("/api/profile", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(changes) }),
    confirm: (id: string) => request<MemoryRecord>(`/api/memory/${encodeURIComponent(id)}/confirm`, { method: "POST" }),
    reject: (id: string) => request<MemoryRecord>(`/api/memory/${encodeURIComponent(id)}/reject`, json({ reason: "Declined in settings" })),
    correct: (id: string, payload: CorrectionPayload) => request<MemoryRecord>(`/api/memory/${encodeURIComponent(id)}/correct`, json(payload)),
    remove: (id: string) => request<null>(`/api/memory/${encodeURIComponent(id)}`, { method: "DELETE" }),
    undo: (id: string) => optionalRoute(() => request<MemoryRecord | null>(MEMORY_ROUTES.undo(id), { method: "POST" })),
    forgetAllToken: () => optionalRoute(() => request<{ confirm_token: string; count?: number; expires_at?: string }>(MEMORY_ROUTES.forgetAllToken, { method: "POST" })),
    forgetAll: (confirmToken: string) => optionalRoute(() => request<{ deleted?: number } | null>(MEMORY_ROUTES.forgetAll, json({ confirm_token: confirmToken }))),
    permissions: () => request<MemoryPermission[]>("/api/memory/permissions"),
    setPreferencePermission: (category: string, allowed: boolean) => request<MemoryPermission>(
      `/api/memory/permissions/${encodeURIComponent(category)}/${PREFERENCE_PURPOSE}`,
      { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ allowed, granularity: allowed ? "explicit_preferences" : "disabled" }) },
    ),
  }), [request]);
}
