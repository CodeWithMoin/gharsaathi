export type VoiceSupport = "ok" | "insecure_context" | "unsupported";
export type VoiceErrorKind = "denied" | "not_found" | "in_use" | "timeout" | "unknown";
export type VoiceFailureView = { kind: Exclude<VoiceSupport, "ok"> | VoiceErrorKind; message: string; retry: boolean };

export const VOICE_START_TIMEOUT_MS: number;

export function voiceSupport(globalLike: { isSecureContext?: boolean; navigator?: { mediaDevices?: { getUserMedia?: unknown } }; RTCPeerConnection?: unknown } | undefined): VoiceSupport;
export function voiceStartError(message: string): Error;
export function voiceStartTimeoutError(): Error;
export function voiceErrorKind(error: unknown): VoiceErrorKind;
export function voiceFailureView(failure?: { support?: "insecure_context" | "unsupported"; error?: unknown }): VoiceFailureView;
export function startDeadline(ms: number, onExpire: (error: Error) => void, setTimer?: (callback: () => void, ms: number) => unknown, clearTimer?: (handle: unknown) => void): { clear(): void };
