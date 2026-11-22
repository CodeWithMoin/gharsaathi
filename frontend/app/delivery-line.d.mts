export type DeliveryLineConfig = { enabled: boolean; inbound_phone_number: string | null; approved_caller_hint?: string | null };

export declare const DELIVERY_LINE_CONFIG_PATH: string;
export declare const DELIVERY_LINE_NUMBER: string;
export declare const FORWARDING_OFF_CODE: string;
export declare function formatDeliveryNumber(number: string | null | undefined): string;
export declare function forwardingCode(number: string | null | undefined): string;
export declare function deliveryLineView(config: DeliveryLineConfig | null | undefined): { number: string; display: string; forwarding: string; off: string; status: string; ready: boolean };
