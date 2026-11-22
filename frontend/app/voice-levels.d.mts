export type VoiceSpeaker = "user" | "assistant" | null;
export type VoicePillStatus = "connecting" | "live" | "closing" | "error";

export const LEVEL_FLOOR_DB: number;
export const LEVEL_CEILING_DB: number;
export const PILL_BAR_COUNT: number;
export const BAR_INTERVAL_MS: number;

export function rmsFromSamples(samples: ArrayLike<number>): number;
export function levelFromRms(rms: number, floorDb?: number, ceilingDb?: number): number;
export function smoothLevel(previous: number, target: number, elapsedMs: number, attackMs?: number, releaseMs?: number): number;
export function createSpeakerTracker(options?: { onLevel?: number; offLevel?: number; holdMs?: number }): {
  update(userLevel: number, assistantLevel: number, nowMs: number): VoiceSpeaker;
  reset(): void;
};
export function createBarHistory(size?: number): {
  bars: { level: number; speaker: VoiceSpeaker }[];
  push(level: number, speaker: VoiceSpeaker): void;
};
export function voicePillLabel(state: { status: VoicePillStatus; busy?: boolean; speaker?: VoiceSpeaker }): "Connecting" | "Listening" | "Thinking" | "Speaking" | "Voice off" | "Ending";
