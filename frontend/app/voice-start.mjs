/**
 * Helpers for the voice start path. Order and rules follow the GPT-Live WebRTC
 * guide (https://developers.openai.com/api/docs/guides/voice-webrtc?api=live):
 * mic track + data channel before the offer, full ICE gathering before the
 * offer is sent (the session request carries one complete SDP; there is no
 * trickle ICE), then the answer. The data channel is pre-negotiated, which the
 * WARP guide says shortens the wait for server events
 * (https://developers.openai.com/api/docs/guides/realtime-webrtc-warp#pre-negotiate-a-data-channel).
 */

/** Channel id sent to the backend as `dcid`; the label stays "oai-events". */
export const VOICE_DATA_CHANNEL_ID = 4;
/** The pending chat save gets this long before the session request goes out. */
export const CHAT_SAVE_WAIT_MS = 150;

/**
 * Content-free stage timer: names and elapsed milliseconds only.
 * @param {() => number} [now]
 */
export function createStartTimer(now = () => performance.now()) {
  const startedAt = now();
  /** @type {Record<string, number>} */
  const stages = {};
  return {
    /** @param {string} stage */
    mark(stage) {
      if (!(stage in stages)) stages[stage] = Math.round(now() - startedAt);
      return stages[stage];
    },
    stages() {
      return { ...stages };
    },
  };
}

/**
 * Waits for `work` at most `ms`. Never rejects: the caller learns whether it
 * finished, failed or ran out of time.
 * @param {Promise<unknown> | undefined} work
 * @param {number} ms
 * @param {(callback: () => void, ms: number) => unknown} [setTimer]
 * @returns {Promise<"done" | "failed" | "timeout">}
 */
export function settleWithin(work, ms, setTimer = (callback, delay) => setTimeout(callback, delay)) {
  if (!work) return Promise.resolve("done");
  return new Promise((resolve) => {
    setTimer(() => resolve("timeout"), ms);
    work.then(() => resolve("done"), () => resolve("failed"));
  });
}

/**
 * Microphone permission without prompting. "unknown" when the browser can't say
 * (e.g. Firefox before it supports the "microphone" name).
 * @param {{ permissions?: { query(descriptor: { name: string }): Promise<{ state: string }> } } | undefined} navigatorLike
 * @returns {Promise<"granted" | "denied" | "prompt" | "unknown">}
 */
export async function microphonePermission(navigatorLike) {
  try {
    const status = await navigatorLike?.permissions?.query({ name: "microphone" });
    const state = status?.state;
    return state === "granted" || state === "denied" || state === "prompt" ? state : "unknown";
  } catch {
    return "unknown";
  }
}

/**
 * A session is billed from creation, so it is only requested before the mic is
 * open when the mic permission is already granted (no prompt can stall it).
 * @param {"granted" | "denied" | "prompt" | "unknown"} permission
 */
export function canRequestSessionBeforeMic(permission) {
  return permission === "granted";
}

/**
 * Throttle for the hover/press prewarm, per page.
 * @param {number} intervalMs
 * @param {() => number} [now]
 */
export function createThrottle(intervalMs, now = () => Date.now()) {
  let last = -Infinity;
  return () => {
    const current = now();
    if (current - last < intervalMs) return false;
    last = current;
    return true;
  };
}
