export type ReplyLanguage = "hinglish" | "hindi" | "english";
export type AddressForm = "masculine" | "feminine" | "neutral";
export type ProfileValues = { first_name: string; address_form: AddressForm; language: ReplyLanguage };
export type ProfileUpdate = { first_name?: string | null; address_form?: AddressForm; language?: ReplyLanguage };
export type SavedAddress = { id: string; address_line?: string; tag?: string; category?: string };

export declare const REPLY_LANGUAGES: ReplyLanguage[];
export declare const ADDRESS_FORMS: AddressForm[];
export function addressLabel(address: SavedAddress | null | undefined): string;
export function selectedAddress<T extends SavedAddress>(addresses: T[], selectedId: string | null | undefined): T | null;
export function addressSummary(addresses: SavedAddress[], selectedId: string | null | undefined): { title: string; detail: string; chosen: boolean };
export function initialEditValues(fields: { key: string; value?: string }[]): Record<string, string>;
export function profileDraft(profile: { first_name?: string | null; address_form?: string; language?: string } | null | undefined, languageLabel?: string): ProfileValues;
export function profileChanges(initial: ProfileValues, draft: ProfileValues): ProfileUpdate | null;
export function disconnectCopy(store: "swiggy" | "zepto" | string): { title: string; body: string; confirmLabel: string; busyLabel: string };
