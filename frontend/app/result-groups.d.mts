export type ResultGroupStatus = "searching" | "done";
export type ResultGroup<P> = { key: string; label: string; query: string; products: P[]; status: ResultGroupStatus; afterMessageId: string | null; seq: number };
export type ResultGroupSnapshot<P> = { item_key: string; item_label: string; query: string; products: P[]; after_message_id: string | null; seq: number };

export const MAX_RESULT_GROUPS: number;
export const MAX_GROUP_PRODUCTS: number;
export function normalizeItemKey(value: unknown): string;
export function resultGroupIdentity(source: { itemKey?: unknown; itemLabel?: unknown; query?: unknown; products?: { name?: unknown; category?: unknown }[] }): { key: string; label: string };
export function upsertResultGroup<P>(groups: ResultGroup<P>[], group: ResultGroup<P>): ResultGroup<P>[];
export function updateResultGroup<P>(groups: ResultGroup<P>[], key: string, patch: Partial<Pick<ResultGroup<P>, "products" | "status">>): ResultGroup<P>[];
export function removeResultGroup<P>(groups: ResultGroup<P>[], key: string): ResultGroup<P>[];
export function resultsHeadline(optionCount: number, shownLimit: number): string;
export function groupBadge(label: string, optionCount: number): string;
export function resultGroupsSnapshot<P>(groups: ResultGroup<P>[]): ResultGroupSnapshot<P>[];
export function restoreResultGroups<P>(saved: { resultGroups?: unknown; products?: P[]; searched?: boolean; submittedQuery?: string; resultsAfterMessageId?: string | null; resultsSeq?: number }): ResultGroup<P>[];
