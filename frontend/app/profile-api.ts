"use client";

import { useCallback } from "react";
import { ApiError, useApiRequest } from "./authenticated-api";
import { normalizeProfile, type HouseholdProfile, type ServerProfile } from "./onboarding-flow.mjs";

export type { AddressForm, HouseholdProfile, ReplyLanguage, ServerProfile } from "./onboarding-flow.mjs";

/** Client for `GET/PUT /api/profile`, the source of truth for name, address form and reply language. */
export const PROFILE_PATH = "/api/profile";

export function useProfileApi() {
  const request = useApiRequest();

  /** Resolves to null on failure so onboarding can still offer the profile step. */
  const loadProfile = useCallback(async (): Promise<ServerProfile | null> => {
    try {
      return await request<ServerProfile>(PROFILE_PATH);
    } catch {
      return null;
    }
  }, [request]);

  const saveProfile = useCallback(async (profile: HouseholdProfile): Promise<HouseholdProfile> => {
    try {
      const saved = await request<ServerProfile>(PROFILE_PATH, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(profile),
      });
      return normalizeProfile(saved) ?? profile;
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 422) throw new Error("Naam save nahi hua. Sirf letters, space, ' . - rakhiye.");
      throw cause;
    }
  }, [request]);

  return { loadProfile, saveProfile };
}
