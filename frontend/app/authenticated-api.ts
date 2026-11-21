"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback } from "react";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

export function useAuthenticatedFetch() {
  const { getToken } = useAuth();

  return useCallback(async (input: RequestInfo | URL, init?: RequestInit) => {
    const token = await getToken();
    if (!token) throw new ApiError("Please sign in to continue.", 401);

    const headers = new Headers(init?.headers);
    headers.set("Authorization", `Bearer ${token}`);
    return fetch(input, { ...init, headers });
  }, [getToken]);
}

export function useApiRequest() {
  const authenticatedFetch = useAuthenticatedFetch();

  return useCallback(async <T,>(path: string, init?: RequestInit): Promise<T> => {
    const response = await authenticatedFetch(`${API_BASE_URL}${path}`, {
      cache: "no-store",
      ...init,
    });
    const payload = response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) {
      throw new ApiError(
        payload && typeof payload.detail === "string" ? payload.detail : "Request failed.",
        response.status,
      );
    }
    return payload as T;
  }, [authenticatedFetch]);
}
