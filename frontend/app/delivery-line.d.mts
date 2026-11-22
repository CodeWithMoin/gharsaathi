export const DIRECTIONS_PATH: string;
export const LINE_PATH: string;
export const LANDMARK_MAX: number;
export const STEP_MAX: number;
export const MAX_STEPS: number;
export const GATE_NOTE_MAX: number;
export const HANDOVER_MAX: number;
export const HANDOVER_CHIPS: string[];

export type DeliveryProvider = "swiggy" | "zepto";
export type SavedDirections = {
  provider: DeliveryProvider;
  address_id: string;
  landmark: string;
  route_directions: string[];
  gate_note: string | null;
  handover_instructions: string | null;
  updated_at?: string | null;
};
export type DirectionsDraft = { landmark: string; steps: string[]; gate_note: string; handover_instructions: string };
export type LinePhase = "none" | "processing" | "ready" | "rejected" | "failed" | "unknown" | "clearing" | "cleared" | "clear_failed";
export type DeliveryLine = {
  phase: LinePhase;
  provider: DeliveryProvider | null;
  address_id: string | null;
  cohort_id: string | null;
  monitor_id: string | null;
  reason: "address_changed" | "delivered" | "cancelled" | "user_cleared" | null;
  error: string | null;
  auto_share: boolean;
  inbound_phone_number: string | null;
  updated_at: string | null;
};
export type InboundDeliveryConfig = {
  enabled: boolean;
  reason: "not_configured" | "owner_not_configured" | "not_owner" | null;
  inbound_phone_number: string | null;
  approved_caller_hint: string | null;
  route_directions_enabled: boolean;
};
export type SelectedDeliveryAddress = { id: string; label?: string | null; text: string };
export type DeliveryLineView = {
  kind: "loading" | "off" | "idle" | "processing" | "ready" | "rejected" | "failed" | "unknown" | "clearing" | "cleared" | "clear_failed";
  tone?: "neutral" | "success" | "warning" | "danger";
  message?: string;
  canShare?: boolean;
};

export function emptyDirectionsDraft(): DirectionsDraft;
export function directionsDraft(saved: SavedDirections | null | undefined): DirectionsDraft;
export function addStep(draft: DirectionsDraft): DirectionsDraft;
export function removeStep(draft: DirectionsDraft, index: number): DirectionsDraft;
export function updateStep(draft: DirectionsDraft, index: number, value: string): DirectionsDraft;
export function pickHandover(draft: DirectionsDraft, chip: string): DirectionsDraft;
export function directionsError(draft: DirectionsDraft): string | null;
export function directionsPayload(draft: DirectionsDraft, provider: DeliveryProvider, addressId: string): Omit<SavedDirections, "updated_at">;
export function directionsDirty(draft: DirectionsDraft, initial: DirectionsDraft): boolean;
export function deliveryLineView(input: {
  config: InboundDeliveryConfig | null;
  address: SelectedDeliveryAddress | null;
  recipientName: string;
  directions: SavedDirections | null;
  line: DeliveryLine | null;
  store: string;
}): DeliveryLineView;
export function stepsSummary(directions: SavedDirections | null | undefined): string[];
