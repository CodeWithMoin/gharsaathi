/**
 * Pure level math for the voice pill's live meter. The component feeds raw
 * analyser samples in; nothing here touches the DOM or Web Audio.
 */

export const LEVEL_FLOOR_DB = -58;
export const LEVEL_CEILING_DB = -14;
export const PILL_BAR_COUNT = 14;
export const BAR_INTERVAL_MS = 70;

/**
 * Root-mean-square of time-domain samples: 0–255 bytes centred on 128
 * (`getByteTimeDomainData`) or −1…1 floats (`getFloatTimeDomainData`).
 * @param {ArrayLike<number>} samples
 */
export function rmsFromSamples(samples) {
  const length = samples.length;
  if (!length) return 0;
  const bytes = samples instanceof Uint8Array;
  let sum = 0;
  for (let index = 0; index < length; index += 1) {
    const value = bytes ? (samples[index] - 128) / 128 : samples[index];
    sum += value * value;
  }
  return Math.sqrt(sum / length);
}

/**
 * Maps RMS to a 0–1 display level on a decibel scale, so quiet speech still
 * moves the meter and loud speech doesn't pin it.
 * @param {number} rms
 */
export function levelFromRms(rms, floorDb = LEVEL_FLOOR_DB, ceilingDb = LEVEL_CEILING_DB) {
  if (!(rms > 0)) return 0;
  const db = 20 * Math.log10(rms);
  return clamp01((db - floorDb) / (ceilingDb - floorDb));
}

/**
 * Envelope follower: rises quickly with the voice, falls back gently. Frame-rate
 * independent, so 30 fps and 120 fps screens look the same.
 * @param {number} previous
 * @param {number} target
 * @param {number} elapsedMs
 */
export function smoothLevel(previous, target, elapsedMs, attackMs = 45, releaseMs = 220) {
  const timeConstant = target > previous ? attackMs : releaseMs;
  if (!(elapsedMs > 0)) return previous;
  const weight = 1 - Math.exp(-elapsedMs / timeConstant);
  return clamp01(previous + (target - previous) * weight);
}

/**
 * Who is audible right now, with hysteresis and a short hold so the label
 * doesn't flicker between words. GharSaathi's own output wins over the mic,
 * because echo can leak into the mic while she speaks.
 * @param {{ onLevel?: number, offLevel?: number, holdMs?: number }} [options]
 */
export function createSpeakerTracker({ onLevel = 0.2, offLevel = 0.08, holdMs = 450 } = {}) {
  /** @type {"user" | "assistant" | null} */
  let speaker = null;
  let lastHeard = -Infinity;
  return {
    /** @param {number} userLevel @param {number} assistantLevel @param {number} nowMs */
    update(userLevel, assistantLevel, nowMs) {
      const threshold = (/** @type {"user" | "assistant"} */ who) => (speaker === who ? offLevel : onLevel);
      /** @type {"user" | "assistant" | null} */
      const heard = assistantLevel >= threshold("assistant") ? "assistant" : userLevel >= threshold("user") ? "user" : null;
      if (heard) {
        speaker = heard;
        lastHeard = nowMs;
      } else if (speaker && nowMs - lastHeard > holdMs) {
        speaker = null;
      }
      return speaker;
    },
    reset() {
      speaker = null;
      lastHeard = -Infinity;
    },
  };
}

/**
 * Fixed-size history of meter bars, newest last, like Voice Memos.
 * @param {number} size
 */
export function createBarHistory(size = PILL_BAR_COUNT) {
  /** @type {{ level: number, speaker: "user" | "assistant" | null }[]} */
  const bars = Array.from({ length: size }, () => ({ level: 0, speaker: null }));
  return {
    bars,
    /** @param {number} level @param {"user" | "assistant" | null} speaker */
    push(level, speaker) {
      bars.shift();
      bars.push({ level: clamp01(level), speaker });
    },
  };
}

/**
 * The single word the pill shows.
 * @param {{ status: "connecting" | "live" | "closing" | "error", busy?: boolean, speaker?: "user" | "assistant" | null }} state
 */
export function voicePillLabel({ status, busy = false, speaker = null }) {
  if (status === "connecting") return "Connecting";
  if (status === "closing") return "Ending";
  if (status === "error") return "Voice off";
  if (speaker === "assistant") return "Speaking";
  if (busy) return "Thinking";
  return "Listening";
}

/** @param {number} value */
function clamp01(value) {
  return value < 0 ? 0 : value > 1 ? 1 : value;
}
