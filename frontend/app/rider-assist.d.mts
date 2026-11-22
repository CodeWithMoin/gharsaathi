export const RIDER_CALLS_PATH: string;
export const RIDER_SETTINGS_PATH: string;
export const RIDER_POLL_MS: number;
export const RIDER_CARD_MAX_AGE_MS: number;
export const RIDER_SETUP_DOC: string;
export const LANDMARK_MAX: number;
export const DIRECTIONS_MAX: number;

export type Handoff = "call_again" | "leave_at_door" | "leave_with_guard" | "hand_to_me";
export type NotesLanguage = "hinglish" | "hindi" | "english";
export const HANDOFF_OPTIONS: { value: Handoff; label: string }[];
export const NOTES_LANGUAGES: { value: NotesLanguage; label: string }[];

export type DeliveryNotes = { landmark: string | null; directions: string | null; handoff: Handoff; preferred_language: NotesLanguage };
export type NotesDraft = { landmark: string; directions: string; handoff: string; preferred_language: string };
export type RiderSettings = { feature_enabled: boolean; owner_configured: boolean; stream_auth_configured: boolean; enabled: boolean; notes: DeliveryNotes };
export type RiderCall = {
  id: string;
  seen: boolean;
  started_at: string;
  ended_at: string | null;
  caller: string;
  delivery_call: boolean | null;
  outcome: "in_progress" | "answered" | "message_taken" | "not_delivery" | "assistant_unavailable" | "ended_early";
  urgency: "low" | "normal" | "high";
  rider_said: string | null;
  summary: string;
  answered_from: ("notes" | "order")[];
};

export function notesDraft(notes: Partial<DeliveryNotes> | null | undefined): NotesDraft;
export function notesError(draft: { landmark: string; directions: string }): string | null;
export function notesPayload(draft: NotesDraft): { landmark: string | null; directions: string | null; handoff: string; preferred_language: string };
export function riderAssistAvailable(settings: { feature_enabled?: boolean; owner_configured?: boolean } | null | undefined): boolean;
export function riderStatus(settings: Omit<RiderSettings, "notes"> | null | undefined): { label: string; active: boolean; canToggle: boolean };
export function riderOutcomeText(call: { outcome: string; answered_from?: string[] }): string;
export function riderCallTime(iso: string): string;
export function riderCallCardText(call: { started_at: string; rider_said?: string | null; outcome: string; answered_from?: string[] }): string;
export function riderToastText(call: { urgency?: string }): string;
export function newRiderCalls<C extends { id: string; seen: boolean; started_at: string }>(calls: C[], toasted: Set<string>): C[];
export function riderCardsToShow<C extends { id: string; started_at: string }>(calls: C[], now: number, dismissed: Set<string>): C[];
export function placeRiderCalls<T extends { kind: string; message?: { id: string } }, C>(items: T[], cards: { card: C; afterMessageId: string | null }[]): (T | { kind: "rider-call"; card: C })[];
