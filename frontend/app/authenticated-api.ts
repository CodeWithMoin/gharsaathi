"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback } from "react";
import { PRIVATE_DEPLOYMENT_EVENT, isPrivateDeploymentRefusal } from "./private-deployment.mjs";

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
    const response = await fetch(input, { ...init, headers });
    if (response.status === 403) {
      const payload = await response.clone().json().catch(() => null);
      if (isPrivateDeploymentRefusal(response.status, payload)) window.dispatchEvent(new Event(PRIVATE_DEPLOYMENT_EVENT));
    }
    return response;
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
