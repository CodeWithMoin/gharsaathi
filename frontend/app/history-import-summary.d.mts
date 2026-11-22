export type HistoryProviderId = "swiggy" | "zepto";

export type HistoryCoverageFields = {
  coverage?: "last_15_days" | "full_history" | null;
  window_start?: string | null;
  window_end?: string | null;
};

export type HistorySummaryResult = HistoryCoverageFields & {
  cached: boolean;
  imported_orders: number;
  detail_failures?: number;
  complete?: boolean;
  stopped_reason?: string | null;
};

export const SWIGGY_HISTORY_DAYS: number;
export function coverageText(provider: HistoryProviderId, fields?: HistoryCoverageFields): string;
export function importSummaryLine(provider: HistoryProviderId, result: HistorySummaryResult): string;
export function fullResyncAvailability(
  status: { full_resync_available_at?: string | null } | null | undefined,
  now?: Date,
): { available: boolean; label: string };
