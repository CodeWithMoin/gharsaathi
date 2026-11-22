/**
 * Whether voice can start in this browser, and what each start failure tells
 * the user. Pure: the voice panel passes in what it reads from `window`.
 */

/** "Connecting…" never lasts longer than this; then the user gets Try again. */
export const VOICE_START_TIMEOUT_MS = 15_000;

const TIMEOUT_ERROR = "VoiceStartTimeoutError";
const USER_FACING_ERROR = "VoiceStartError";

/**
 * Checked before anything else, so no session is created where the mic can
 * never open. Browsers hide `navigator.mediaDevices` outside secure contexts
 * (https or localhost), e.g. a phone opening `http://<LAN-IP>:3003`.
 * @param {{ isSecureContext?: boolean, navigator?: { mediaDevices?: { getUserMedia?: unknown } }, RTCPeerConnection?: unknown } | undefined} globalLike
 * @returns {"ok" | "insecure_context" | "unsupported"}
 */
export function voiceSupport(globalLike) {
  if (!globalLike || globalLike.isSecureContext === false) return "insecure_context";
  if (typeof globalLike.navigator?.mediaDevices?.getUserMedia !== "function") return globalLike.isSecureContext ? "unsupported" : "insecure_context";
  if (typeof globalLike.RTCPeerConnection !== "function") return "unsupported";
  return "ok";
}

/** An error whose message is already written for the user. */
export function voiceStartError(message) {
  const error = new Error(message);
  error.name = USER_FACING_ERROR;
  return error;
}

export function voiceStartTimeoutError() {
  const error = new Error("Voice took too long to connect.");
  error.name = TIMEOUT_ERROR;
  return error;
}

/**
 * @param {unknown} error
 * @returns {"denied" | "not_found" | "in_use" | "timeout" | "unknown"}
 */
export function voiceErrorKind(error) {
  const name = error && typeof error === "object" && "name" in error ? error.name : "";
  if (name === "NotAllowedError" || name === "PermissionDeniedError" || name === "SecurityError") return "denied";
  if (name === "NotFoundError" || name === "DevicesNotFoundError" || name === "OverconstrainedError" || name === "ConstraintNotSatisfiedError") return "not_found";
  if (name === "NotReadableError" || name === "TrackStartError" || name === "AbortError") return "in_use";
  if (name === TIMEOUT_ERROR) return "timeout";
  return "unknown";
}

const MESSAGES = {
  insecure_context: "Voice needs a secure connection (https) or localhost. Open the app on https, or use typed chat.",
  unsupported: "This browser can't use the microphone for voice. Use typed chat instead.",
  denied: "Allow microphone access in your browser settings, then tap Try again. Or use typed chat.",
  not_found: "No microphone was found. Connect one and tap Try again, or use typed chat.",
  in_use: "Your microphone is being used by another app. Close it and tap Try again, or use typed chat.",
  timeout: "Voice took too long to connect. Tap Try again, or use typed chat.",
  unknown: "Could not start voice. Tap Try again, or use typed chat.",
};

/**
 * What the pill shows for a failure: `retry` is false where trying again
 * cannot help (the page itself has to change).
 * @param {{ support?: "insecure_context" | "unsupported", error?: unknown }} failure
 */
export function voiceFailureView({ support, error } = {}) {
  if (support === "insecure_context" || support === "unsupported") return { kind: support, message: MESSAGES[support], retry: false };
  const kind = voiceErrorKind(error);
  const own = kind === "unknown" && error instanceof Error && error.name === USER_FACING_ERROR && error.message;
  return { kind, message: own || MESSAGES[kind], retry: true };
}

/**
 * One deadline for the whole start. `onExpire` gets a timeout error once,
 * unless `clear()` ran first.
 * @param {number} ms
 * @param {(error: Error) => void} onExpire
 * @param {(callback: () => void, ms: number) => unknown} [setTimer]
 * @param {(handle: unknown) => void} [clearTimer]
 */
export function startDeadline(ms, onExpire, setTimer = (callback, delay) => setTimeout(callback, delay), clearTimer = (handle) => clearTimeout(/** @type {any} */ (handle))) {
  let done = false;
  const handle = setTimer(() => {
    if (done) return;
    done = true;
    onExpire(voiceStartTimeoutError());
  }, ms);
  return {
    clear() {
      if (done) return;
      done = true;
      clearTimer(handle);
    },
  };
}
