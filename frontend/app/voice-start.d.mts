export type MicPermission = "granted" | "denied" | "prompt" | "unknown";

export const VOICE_DATA_CHANNEL_ID: number;
export const CHAT_SAVE_WAIT_MS: number;

export function createStartTimer(now?: () => number): {
  mark(stage: string): number;
  stages(): Record<string, number>;
};
export function settleWithin(work: Promise<unknown> | undefined, ms: number, setTimer?: (callback: () => void, ms: number) => unknown): Promise<"done" | "failed" | "timeout">;
export function microphonePermission(navigatorLike: { permissions?: { query(descriptor: { name: string }): Promise<{ state: string }> } } | undefined): Promise<MicPermission>;
export function canRequestSessionBeforeMic(permission: MicPermission): boolean;
export function createThrottle(intervalMs: number, now?: () => number): () => boolean;
