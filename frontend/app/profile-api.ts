"use client";

import { useCallback } from "react";
import { ApiError, useApiRequest } from "./authenticated-api";
import { normalizeProfile, type HouseholdProfile } from "./onboarding-flow.mjs";

export type { AddressForm, HouseholdProfile, ReplyLanguage } from "./onboarding-flow.mjs";

/**
 * Client for the server profile API (`GET/PUT /api/profile`). Until that route is
 * deployed, a 404/405 is reported as `available: false` so callers can rely on the
 * Clerk metadata copy instead of failing onboarding.
 */
export const PROFILE_PATH = "/api/profile";

export type ProfileLoadResult = { profile: HouseholdProfile | null; available: boolean };
export type ProfileSaveResult = { profile: HouseholdProfile; savedToServer: boolean };

function routeMissing(cause: unknown) {
  return cause instanceof ApiError && (cause.status === 404 || cause.status === 405);
}

export function useProfileApi() {
  const request = useApiRequest();

  const loadProfile = useCallback(async (): Promise<ProfileLoadResult> => {
    try {
      return { profile: normalizeProfile(await request<unknown>(PROFILE_PATH)), available: true };
    } catch (cause) {
      // A missing profile and a missing route both mean "use the fallback copy".
      // Other failures are not fatal either: the fallback still lets the user continue.
      return { profile: null, available: !routeMissing(cause) && !(cause instanceof TypeError) };
    }
  }, [request]);

  const saveProfile = useCallback(async (profile: HouseholdProfile): Promise<ProfileSaveResult> => {
    try {
      const saved = await request<unknown>(PROFILE_PATH, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(profile),
      });
      return { profile: normalizeProfile(saved) ?? profile, savedToServer: true };
    } catch (cause) {
      if (routeMissing(cause)) return { profile, savedToServer: false };
      throw cause;
    }
  }, [request]);

  return { loadProfile, saveProfile };
}
