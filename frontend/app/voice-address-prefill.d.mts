export type AddressPrefillField = "type" | "name" | "flat_details" | "building_name" | "floor" | "landmark" | "short_address" | "formatted_address" | "contact_name";
export type AddressPrefill = Partial<Record<AddressPrefillField, string>>;
export function sanitizeAddressPrefill(raw: unknown): AddressPrefill;
