export type MemoryOrigin = "stated" | "explicit" | "inferred";
export type MemoryGroupId = "pasand" | "kabhi_nahi" | "niyam";

export type MemoryRecord = {
  id: string;
  memory_type: string;
  value: Record<string, unknown>;
  text: string;
  source_type?: string;
  source_conversation_id?: string | null;
  status: "proposed" | "confirmed" | "rejected" | "superseded" | "deleted" | string;
  sensitivity?: string;
  confidence?: number;
  allowed_purposes?: string[];
  expires_at?: string | null;
  confirmed_at?: string | null;
  deleted_at?: string | null;
  created_at?: string;
  updated_at?: string;
  /** Added by the memory-write-manage backend; absent on older servers. */
  origin?: MemoryOrigin | null;
  learned_at?: string | null;
};

export type MemoryRow = {
  id: string;
  memory: MemoryRecord;
  group: MemoryGroupId;
  sentence: string;
  badge: string;
  date: string;
  learnedAt: number | null;
  undoable: boolean;
};

export type MemoryGroup = { id: MemoryGroupId; title: string; rows: MemoryRow[] };
export type FieldOption = { value: string; label: string };
export type EditableField = { key: string; label: string; value: string; options?: FieldOption[] };
export type SummaryGroupId = "home" | "food" | "usual_buys" | "avoid" | "shopping_style" | "from_orders";
export type SummarySource = "told" | "in_passing" | "repeated_choice" | "order_history";
export type SummaryItem = {
  id: string;
  group: SummaryGroupId;
  kind?: string | null;
  label: string;
  source: SummarySource;
  source_label?: string;
  updated_at?: string | null;
  managed_by?: "memory" | "profile" | "order";
  item?: string | null;
  value?: string | null;
};
export type MemorySummaryResponse = {
  consent: { saved_preferences: "on" | "off"; purchase_history: "on" | "off" };
  groups: { id: SummaryGroupId; title: string; items: SummaryItem[] }[];
  suggestions: SummaryItem[];
  total: number;
};
export type SummaryRow = {
  id: string;
  label: string;
  kind: string | null;
  source: SummarySource;
  badge: string;
  date: string;
  managedBy: "memory" | "profile" | "order";
  value: string | null;
  record: MemoryRecord | null;
  undoable: boolean;
  editable: boolean;
  deletable: boolean;
};
export type SummaryView = {
  groups: { id: SummaryGroupId; title: string; rows: SummaryRow[] }[];
  suggestions: SummaryRow[];
  total: number;
  deletable: number;
  consent: "on" | "off";
  history: "on" | "off";
};
export type CorrectionPayload = {
  memory_type: string;
  value: Record<string, unknown>;
  text: string;
  source_type: "correction";
  source_conversation_id: string | null;
  sensitivity: string;
  confidence: number;
  allowed_purposes: string[];
  reason: string;
};
export type PermissionLike = { data_category: string; purpose: string; allowed: boolean; revoked_at?: string | null };

export const UNDO_WINDOW_MS: number;
export const GROUPS: readonly { id: MemoryGroupId; title: string }[];
export const BADGES: { stated_recent: string; stated: string; explicit: string; inferred: string };
export function productLabel(value: unknown): string;
export function storeLabel(value: unknown): string;
export function groupFor(memory: Pick<MemoryRecord, "memory_type">): MemoryGroupId;
export function memorySentence(memory: Pick<MemoryRecord, "memory_type" | "value" | "text">): string;
export function learnedAt(memory: Partial<MemoryRecord>): number | null;
export function originOf(memory: Partial<MemoryRecord>): MemoryOrigin;
export function isRecentlyLearned(memory: Partial<MemoryRecord>, now?: number): boolean;
export function badgeFor(memory: Partial<MemoryRecord>, now?: number): string;
export function canUndo(memory: Partial<MemoryRecord>, now?: number): boolean;
export function formatMemoryDate(time: number | null, now?: number): string;
export function groupMemories(memories: MemoryRecord[], now?: number): { groups: MemoryGroup[]; suggestions: MemoryRow[]; total: number };
export function editableFields(memory: Pick<MemoryRecord, "value" | "text">): EditableField[];
export function correctionPayload(memory: MemoryRecord, edits: Record<string, string>): CorrectionPayload | null;
export const FIELD_OPTIONS: Record<string, FieldOption[]>;
export const SUMMARY_GROUPS: readonly { id: SummaryGroupId; title: string }[];
export const SOURCE_LABELS: Record<SummarySource, string>;
export function summaryView(summary: MemorySummaryResponse | null | undefined, records: MemoryRecord[], now?: number): SummaryView;
export function profileResetPayload(kind: string | null): { first_name?: null; language?: "hinglish"; address_form?: "neutral" } | null;
export function preferencePermissionState(permissions: PermissionLike[], categories: readonly string[], purpose?: string): "on" | "off" | "partial";
