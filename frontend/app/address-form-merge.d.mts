import type { AddressPrefill } from "./voice-address-prefill.mjs";

export type AddressFormValues = Partial<Record<"type" | "name" | "flat_details" | "building_name" | "floor" | "formatted_address" | "short_address" | "landmark" | "contact_name" | "contact_number", string>>;
export type AddressFormAck = { form_visible: boolean; reason: string; filled_fields: string[]; kept_user_fields: string[] };

export declare const ADDRESS_TEXT_FIELDS: string[];
export declare const REQUIRED_FOCUS_ORDER: string[];
export declare const ACK_TIMEOUT_MS: number;
export function mergeAddressPrefill(values: AddressFormValues, incoming: AddressPrefill, touched: Set<string>): { values: AddressFormValues; filled: string[]; kept: string[] };
export function firstEmptyField(values: AddressFormValues): string | null;
export function settleAddressFormAck(result: unknown, options?: { timeoutMs?: number }): Promise<AddressFormAck>;
