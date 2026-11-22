"use client";

import { useEffect, useState } from "react";
import { useApiRequest } from "./authenticated-api";
import { DELIVERY_LINE_CONFIG_PATH, type DeliveryLineConfig } from "./delivery-line-settings.mjs";

/** The Sarvam delivery line's server config: `undefined` while loading, `null` if it could not be read. */
export function useDeliveryLineConfig() {
  const request = useApiRequest();
  const [config, setConfig] = useState<DeliveryLineConfig | null | undefined>(undefined);
  useEffect(() => {
    let active = true;
    void request<DeliveryLineConfig>(DELIVERY_LINE_CONFIG_PATH)
      .then((loaded) => { if (active) setConfig(loaded); })
      .catch(() => { if (active) setConfig(null); });
    return () => { active = false; };
  }, [request]);
  return config;
}
