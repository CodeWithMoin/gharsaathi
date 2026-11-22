export type BestPickBadge = "You bought this before" | "Your usual" | "Best match";

export type OptionAnnotations = { remembered?: string | null; preferred?: boolean | null; avoided?: boolean | null; available?: boolean };
export type OrderableOption = OptionAnnotations & { provider?: string; product_id: string; sku_id: string; name?: string };
export type OrderedOption<P> = { key: string; product: P; number: number; badge: BestPickBadge | null };
export type BasketLike = { lines: { ref: { provider: string; variant_id: string }; quantity: number }[] };

export const MAX_OPTIONS: number;
export function optionKey(product: { provider?: string; product_id: string; sku_id: string }): string;
export function bestPickBadge(product: OptionAnnotations): BestPickBadge | null;
export function orderOptions<P extends OrderableOption>(products: P[], limit?: number): OrderedOption<P>[];
export function positionLabel(index: number, total: number): string;
export function optionPositionLabel(number: number, total: number): string;
export function storeName(product: { provider?: string }, commerceMode?: "demo" | "live"): string;
export function optionTitle(product: { name: string; pack_size?: string | null }): string;
export function basketActionLabel(product: { name: string; pack_size?: string | null; provider?: string }, action: "add" | "increase" | "decrease" | "remove"): string;
export function optionPrices(product: { price?: { offer_display?: string | null; mrp_display?: string | null; saving_display?: string | null } }): { price: string | null; mrp: string | null; saving: string | null };
export function basketQuantity(baskets: Partial<Record<string, BasketLike | undefined>>, product: { provider?: string; spin_id?: string }): number;
export function activeIndexFromScroll(offsets: number[], scrollLeft: number, maxScroll: number): number;
