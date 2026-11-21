// Pure turn-taking logic shared by the browser (smart-turn.ts) and the offline
// evaluation (scripts/eval-smart-turn.mjs). No browser APIs here.

export const SAMPLE_RATE = 16_000;
export const WINDOW_SAMPLES = SAMPLE_RATE * 4;
export const MODEL_SHA256 = "39736bc2c4a1ff303d3bd6c54ac648d175f921624e232680c3e097e0e46bb966";

export const DEFAULT_TURN_OPTIONS = Object.freeze({
  // Tuned offline on Hinglish TTS clips (docs/voice-turn-taking.md).
  completeThreshold: 0.7,
  // Consecutive confident checks needed before ending the turn.
  confirmChecks: 2,
  silenceBeforeCheckMs: 200,
  checkIntervalMs: 200,
  silenceTimeoutMs: 1_600,
  speechRms: 0.012,
  minSpeechMs: 80,
  // While assistant audio is audible, the mic must clearly beat the echo.
  bargeInRms: 0.03,
  bargeInEchoRatio: 0.4,
  bargeInMs: 200,
  outputActiveRms: 0.01,
});

/**
 * Turn gate state machine. Feed it audio frames; it returns actions:
 * - { type: "hold" }      user started a turn: mute assistant playback
 * - { type: "yield" }     assistant is audible during the user's turn: ask it to stop (once per turn)
 * - { type: "check" }     run the end-of-turn model on the current window
 * - { type: "release", reason, probability } user's turn is complete: resume playback
 */
export function createTurnGate(overrides = {}) {
  const options = { ...DEFAULT_TURN_OPTIONS, ...overrides };
  let inTurn = false;
  let candidateSince = null;
  let lastSpeechAt = 0;
  let lastCheckAt = -Infinity;
  let checking = false;
  let yielded = false;
  let confident = 0;

  function speechNow(micRms, outputRms) {
    const outputAudible = !inTurn && outputRms >= options.outputActiveRms;
    if (outputAudible) {
      const needed = Math.max(options.bargeInRms, outputRms * options.bargeInEchoRatio);
      return { speaking: micRms >= needed, holdMs: options.bargeInMs, outputAudible };
    }
    return { speaking: micRms >= options.speechRms, holdMs: options.minSpeechMs, outputAudible };
  }

  return {
    options,
    get inTurn() { return inTurn; },
    frame({ now, micRms, outputRms = 0 }) {
      const actions = [];
      const { speaking, holdMs, outputAudible } = speechNow(micRms, outputRms);
      if (speaking) {
        if (!inTurn) {
          candidateSince ??= now;
          if (now - candidateSince >= holdMs) {
            inTurn = true;
            yielded = false;
            lastSpeechAt = now;
            actions.push({ type: "hold" });
            if (outputAudible) {
              yielded = true;
              actions.push({ type: "yield" });
            }
          }
        } else {
          lastSpeechAt = now;
        }
      } else if (!inTurn) {
        candidateSince = null;
      }
      if (!inTurn) return actions;
      if (!yielded && outputRms >= options.outputActiveRms) {
        // Assistant started talking while the user's turn is still open.
        yielded = true;
        actions.push({ type: "yield" });
      }
      if (speaking) {
        confident = 0;
        return actions;
      }
      const silence = now - lastSpeechAt;
      if (silence >= options.silenceTimeoutMs) {
        actions.push(finish("silence-timeout", 0));
        return actions;
      }
      if (!checking && silence >= options.silenceBeforeCheckMs && now - lastCheckAt >= options.checkIntervalMs) {
        lastCheckAt = now;
        checking = true;
        actions.push({ type: "check" });
      }
      return actions;
    },
    modelResult(probability) {
      checking = false;
      if (!inTurn) return [];
      confident = probability >= options.completeThreshold ? confident + 1 : 0;
      if (confident >= options.confirmChecks) return [finish("model", probability)];
      return [];
    },
    modelFailed() {
      checking = false;
      return [];
    },
    reset() {
      inTurn = false;
      candidateSince = null;
      checking = false;
      yielded = false;
    },
  };

  function finish(reason, probability) {
    inTurn = false;
    candidateSince = null;
    checking = false;
    confident = 0;
    lastCheckAt = -Infinity;
    return { type: "release", reason, probability };
  }
}

export function rms(samples) {
  if (!samples.length) return 0;
  let sum = 0;
  for (const sample of samples) sum += sample * sample;
  return Math.sqrt(sum / samples.length);
}

export function downsample(input, inputRate, outputRate = SAMPLE_RATE) {
  if (inputRate === outputRate) return Float32Array.from(input);
  const ratio = inputRate / outputRate;
  const length = Math.max(1, Math.floor(input.length / ratio));
  const output = new Float32Array(length);
  for (let index = 0; index < length; index += 1) {
    const start = Math.floor(index * ratio);
    const end = Math.min(input.length, Math.floor((index + 1) * ratio));
    let sum = 0;
    for (let cursor = start; cursor < end; cursor += 1) sum += input[cursor];
    output[index] = sum / Math.max(1, end - start);
  }
  return output;
}

/** Ring buffer holding the last 4 s of 16 kHz audio. */
export function createWindow() {
  const samples = new Float32Array(WINDOW_SAMPLES);
  let writeIndex = 0;
  let count = 0;
  return {
    push(chunk) {
      for (let index = 0; index < chunk.length; index += 1) {
        samples[writeIndex] = chunk[index];
        writeIndex = (writeIndex + 1) % WINDOW_SAMPLES;
      }
      count = Math.min(WINDOW_SAMPLES, count + chunk.length);
    },
    /** Right-aligned, peak-normalised model input (matches smart_turn/infer.py). */
    snapshot() {
      const output = new Float32Array(WINDOW_SAMPLES);
      if (count < WINDOW_SAMPLES) {
        const start = (writeIndex - count + WINDOW_SAMPLES) % WINDOW_SAMPLES;
        for (let index = 0; index < count; index += 1) {
          output[WINDOW_SAMPLES - count + index] = samples[(start + index) % WINDOW_SAMPLES];
        }
      } else {
        output.set(samples.subarray(writeIndex), 0);
        output.set(samples.subarray(0, writeIndex), WINDOW_SAMPLES - writeIndex);
      }
      return peakNormalise(output);
    },
    clear() {
      samples.fill(0);
      writeIndex = 0;
      count = 0;
    },
  };
}

export function peakNormalise(samples) {
  let peak = 0;
  for (const sample of samples) peak = Math.max(peak, Math.abs(sample));
  if (peak > 0) for (let index = 0; index < samples.length; index += 1) samples[index] /= peak;
  return samples;
}

export function hex(buffer) {
  return Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
