export const INBOUND_CALLS_PATH: string;
export const INBOUND_CONFIG_PATH: string;
export const INBOUND_POLL_MS: number;
export const INBOUND_CARD_MAX_AGE_MS: number;

export type InboundCallOutcome =
  | "route_confirmed"
  | "owner_transfer_requested"
  | "provider_contact_needed"
  | "unauthorized_caller"
  | "general_request_handled"
  | "out_of_scope"
  | "no_decision";
export const CALL_OUTCOMES: InboundCallOutcome[];

export type InboundCall = {
  id: string;
  started_at: string;
  provider: "zepto" | "swiggy" | null;
  outcome: InboundCallOutcome;
  summary: string | null;
  caller: string | null;
};

export function inboundStoreName(provider: string | null | undefined): string | null;
export function inboundOutcomeText(outcome: string, provider: string | null | undefined): string;
export function inboundHeadline(provider: string | null | undefined, outcome: string): string;
export function inboundCallTime(iso: string): string;
export function inboundCallCardText(call: { started_at: string; provider?: string | null; outcome: string; summary?: string | null }): string;
export function inboundCallUrgent(call: { outcome: string }): boolean;
export function inboundCardsToShow<C extends { id: string; started_at: string }>(calls: C[], now: number, dismissed: Set<string>): C[];
export function riderDirectionsLine(calls: { started_at: string; provider?: string | null; outcome: string }[], order: { store: string; placedAt?: string | null; status?: string }): string | null;
export function placeInboundCalls<T extends { kind: string }, C>(items: T[], cards: C[]): (T | { kind: "inbound-call"; card: C })[];
