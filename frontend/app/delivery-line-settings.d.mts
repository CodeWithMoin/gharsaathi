import type { InboundDeliveryConfig } from "./delivery-line.mjs";

/** The server config, as read by delivery-line-panel.tsx too. */
export type DeliveryLineConfig = Pick<InboundDeliveryConfig, "enabled" | "inbound_phone_number"> & Partial<InboundDeliveryConfig>;

export declare const DELIVERY_LINE_CONFIG_PATH: string;
export declare const DELIVERY_LINE_NUMBER: string;
export declare const FORWARDING_OFF_CODE: string;
export declare function formatDeliveryNumber(number: string | null | undefined): string;
export declare function forwardingCode(number: string | null | undefined): string;
export declare function deliveryLineSettingsView(config: DeliveryLineConfig | null | undefined): { number: string; display: string; forwarding: string; off: string; status: string; ready: boolean };
