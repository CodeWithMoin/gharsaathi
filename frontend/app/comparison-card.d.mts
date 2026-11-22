import type { Product } from "./product-comparison";

export type ComparisonColumn = { product: Product & { provider: "swiggy_instamart" | "zepto" }; label: string; unitPrice: string; badges: string[] };
export type Comparison = { id: string; itemLabel: string; aspect: string; headline: string; columns: ComparisonColumn[] };
export type LiveComparisonCard = { comparison: Comparison; afterMessageId: string | null };

export const MAX_COLUMNS: number;
export function comparisonFromEvent(raw: unknown, now?: number): Comparison | null;
export function comparisonTitle(comparison: Comparison): string;
export function unitPriceText(column: ComparisonColumn): string;
export function withComparisonCard<T extends { kind: string; message?: { id: string } }>(items: T[], card: LiveComparisonCard | null): (T | { kind: "comparison"; card: LiveComparisonCard })[];
