export type StoreId = "zepto" | "swiggy";
export type SavedAddressLike = { id: string; address_line: string; tag?: string; category?: string };
export type StoreAddresses = { connected: boolean | null; addresses: SavedAddressLike[]; selectedId: string; loaded?: boolean };
export type HeaderAddress = { state: "selected" | "choose" | "connect" | "checking"; store: StoreId | null; label: string; line: string; accessibleLabel: string };
export type AddressGate = { store: StoreId; storeName: string; addresses: SavedAddressLike[]; canAdd: boolean };
export type OrderCardLink = { id: "delivery-line" | "directions" | "help"; label: string };

export declare const STORE_NAMES: Readonly<Record<StoreId, string>>;
export declare function addressLabel(address: { tag?: string; category?: string } | null | undefined): string;
export declare function headerAddress(stores: Record<StoreId, StoreAddresses>): HeaderAddress;
export declare function addressGate(stores: Record<StoreId, StoreAddresses>): AddressGate | null;
export declare function orderCardLinks(options: { final: boolean; deliveryLineReady: boolean }): OrderCardLink[];
