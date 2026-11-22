"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useApiRequest } from "./authenticated-api";

export type StoreProvider = "swiggy" | "zepto";
export type CommerceMode = "demo" | "live";
export type StoreAddress = { id: string; address_line: string; category?: string; tag?: string };
export type StoreAddressList = { addresses: StoreAddress[]; resolution: { default_address_id?: string | null }; needs_refresh?: boolean };

export type StoreState = {
  connection: "checking" | "connected" | "disconnected" | "error";
  connectionError: string;
  connecting: boolean;
  connectError: string;
  addressState: "idle" | "loading" | "ready" | "needs_refresh" | "error";
  addressError: string;
  addresses: StoreAddress[];
  selectedAddressId: string;
  selecting: boolean;
};

export const STORE_PROVIDERS: StoreProvider[] = ["zepto", "swiggy"];
export const storeName = (provider: StoreProvider) => provider === "swiggy" ? "Swiggy Instamart" : "Zepto";

const initialStore: StoreState = {
  connection: "checking",
  connectionError: "",
  connecting: false,
  connectError: "",
  addressState: "idle",
  addressError: "",
  addresses: [],
  selectedAddressId: "",
  selecting: false,
};

/**
 * Store connection and delivery-address state for onboarding. It uses the same
 * endpoints as the setup sheet, and only contacts a provider when the user taps.
 */
export function useStoreSetup(enabled: boolean) {
  const request = useApiRequest();
  const [stores, setStores] = useState<Record<StoreProvider, StoreState>>({ swiggy: initialStore, zepto: initialStore });
  const [commerceMode, setCommerceMode] = useState<CommerceMode | null>(null);
  const addressRequests = useRef<Partial<Record<StoreProvider, Promise<void>>>>({});
  const storesRef = useRef(stores);
  useEffect(() => { storesRef.current = stores; }, [stores]);

  const patch = useCallback((provider: StoreProvider, next: Partial<StoreState>) => {
    setStores((current) => ({ ...current, [provider]: { ...current[provider], ...next } }));
  }, []);

  const checkConnection = useCallback(async (provider: StoreProvider) => {
    patch(provider, { connection: "checking", connectionError: "" });
    try {
      const data = await request<{ connected: boolean; mode?: CommerceMode }>(`/api/${provider}/connection`);
      patch(provider, { connection: data.connected ? "connected" : "disconnected" });
      if (provider === "swiggy" || data.mode === "demo") setCommerceMode((current) => data.mode === "demo" ? "demo" : current ?? data.mode ?? "live");
    } catch (cause) {
      patch(provider, { connection: "error", connectionError: cause instanceof Error ? cause.message : `Could not check ${storeName(provider)}.` });
    }
  }, [patch, request]);

  useEffect(() => {
    if (!enabled) return;
    void checkConnection("swiggy");
    void checkConnection("zepto");
  }, [checkConnection, enabled]);

  const applyAddresses = useCallback((provider: StoreProvider, data: StoreAddressList) => {
    setStores((current) => {
      const previous = current[provider];
      const preferred = data.resolution.default_address_id;
      const keep = data.addresses.some((item) => item.id === previous.selectedAddressId) ? previous.selectedAddressId : "";
      const selectedAddressId = keep
        || (preferred && data.addresses.some((item) => item.id === preferred) ? preferred : "")
        || (data.addresses.length === 1 ? data.addresses[0].id : "");
      return {
        ...current,
        [provider]: {
          ...previous,
          addresses: data.addresses,
          selectedAddressId,
          addressError: "",
          addressState: data.addresses.length ? "ready" : data.needs_refresh ? "needs_refresh" : "ready",
        },
      };
    });
  }, []);

  const loadAddresses = useCallback((provider: StoreProvider, refresh = false) => {
    const inFlight = addressRequests.current[provider];
    if (inFlight) return inFlight;
    const pending = (async () => {
      patch(provider, { addressState: "loading", addressError: "" });
      try {
        applyAddresses(provider, await request<StoreAddressList>(`/api/${provider}/addresses${refresh ? "?refresh=true" : ""}`));
      } catch (cause) {
        patch(provider, { addressState: "error", addressError: cause instanceof Error ? cause.message : `Could not load ${storeName(provider)} addresses.` });
      } finally {
        delete addressRequests.current[provider];
      }
    })();
    addressRequests.current[provider] = pending;
    return pending;
  }, [applyAddresses, patch, request]);

  const selectAddress = useCallback(async (provider: StoreProvider, addressId: string) => {
    const previous = storesRef.current[provider].selectedAddressId;
    patch(provider, { selectedAddressId: addressId, selecting: true, addressError: "" });
    try {
      await request(`/api/${provider}/addresses/selected`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ address_id: addressId }),
      });
      patch(provider, { selecting: false });
    } catch (cause) {
      patch(provider, { selecting: false, selectedAddressId: previous, addressError: cause instanceof Error ? cause.message : "Could not save this address. Please try again." });
    }
  }, [patch, request]);

  /** Checks the callback listener first (AGENTS.md), then leaves for the provider's own login page. */
  const connect = useCallback(async (provider: StoreProvider) => {
    patch(provider, { connecting: true, connectError: "" });
    try {
      const readiness = await request<{ ready: boolean; message: string }>(`/api/${provider}/oauth/readiness`);
      if (!readiness.ready) {
        patch(provider, { connecting: false, connectError: readiness.message || `${storeName(provider)} sign-in is not ready yet.` });
        return;
      }
      const { authorization_url: authorizationUrl } = await request<{ authorization_url: string }>(`/api/${provider}/oauth/authorization`, { method: "POST" });
      window.location.assign(authorizationUrl);
    } catch (cause) {
      patch(provider, { connecting: false, connectError: cause instanceof Error ? cause.message : `Could not prepare ${storeName(provider)} sign-in.` });
    }
  }, [patch, request]);

  return { stores, commerceMode, checkConnection, connect, loadAddresses, applyAddresses, selectAddress };
}
