import assert from "node:assert/strict";
import test from "node:test";
import { BAR_INTERVAL_MS, PILL_BAR_COUNT, createBarHistory, createSpeakerTracker, levelFromRms, rmsFromSamples, smoothLevel, voicePillLabel } from "../../app/voice-levels.mjs";

function sine(amplitude, length = 512) {
  return Float32Array.from({ length }, (_, index) => amplitude * Math.sin((index / length) * Math.PI * 16));
}

test("RMS of silence is zero for byte and float samples", () => {
  assert.equal(rmsFromSamples(new Uint8Array(512).fill(128)), 0);
  assert.equal(rmsFromSamples(new Float32Array(512)), 0);
  assert.equal(rmsFromSamples([]), 0);
});

test("RMS of a sine is amplitude over root two, for both sample formats", () => {
  assert.ok(Math.abs(rmsFromSamples(sine(0.5)) - 0.5 / Math.SQRT2) < 1e-3);
  const bytes = Uint8Array.from(sine(0.5), (value) => Math.round(128 + value * 128));
  assert.ok(Math.abs(rmsFromSamples(bytes) - 0.5 / Math.SQRT2) < 0.01);
});

test("levels use a decibel scale clamped to 0–1", () => {
  assert.equal(levelFromRms(0), 0);
  assert.equal(levelFromRms(Number.NaN), 0);
  assert.equal(levelFromRms(10 ** (-70 / 20)), 0);
  assert.equal(levelFromRms(1), 1);
  const quiet = levelFromRms(10 ** (-46 / 20));
  const loud = levelFromRms(10 ** (-24 / 20));
  assert.ok(quiet > 0.2 && quiet < 0.35, `quiet speech still moves the meter (${quiet})`);
  assert.ok(loud > quiet && loud < 1);
});

test("smoothing rises fast, falls slowly, and ignores frame rate", () => {
  const rise = smoothLevel(0, 1, 50);
  const fall = 1 - smoothLevel(1, 0, 50);
  assert.ok(rise > 0.6, `attack ${rise}`);
  assert.ok(fall < 0.25, `release ${fall}`);
  let at60 = 0;
  for (let frame = 0; frame < 12; frame += 1) at60 = smoothLevel(at60, 1, 1000 / 60);
  let at120 = 0;
  for (let frame = 0; frame < 24; frame += 1) at120 = smoothLevel(at120, 1, 1000 / 120);
  assert.ok(Math.abs(at60 - at120) < 1e-9);
  assert.equal(smoothLevel(0.4, 1, 0), 0.4);
  assert.ok(smoothLevel(0, 5, 1000) <= 1);
});

test("speaker tracking has hysteresis, a hold, and prefers GharSaathi's output", () => {
  const tracker = createSpeakerTracker({ onLevel: 0.2, offLevel: 0.08, holdMs: 400 });
  assert.equal(tracker.update(0.1, 0, 0), null, "below the start level");
  assert.equal(tracker.update(0.3, 0, 10), "user");
  assert.equal(tracker.update(0.1, 0, 20), "user", "stays on above the lower stop level");
  assert.equal(tracker.update(0, 0, 300), "user", "held between words");
  assert.equal(tracker.update(0, 0, 500), null, "released after the hold");
  assert.equal(tracker.update(0.5, 0.4, 600), "assistant", "echo in the mic doesn't steal her turn");
  tracker.reset();
  assert.equal(tracker.update(0, 0, 700), null);
});

test("bar history keeps a fixed number of bars, newest last", () => {
  const history = createBarHistory();
  assert.equal(history.bars.length, PILL_BAR_COUNT);
  history.push(0.5, "user");
  history.push(2, "assistant");
  assert.equal(history.bars.length, PILL_BAR_COUNT);
  assert.deepEqual(history.bars.at(-1), { level: 1, speaker: "assistant" });
  assert.deepEqual(history.bars.at(-2), { level: 0.5, speaker: "user" });
  assert.ok(BAR_INTERVAL_MS >= 1000 / 60, "bars never scroll faster than frames");
});

test("the pill shows one short state word", () => {
  assert.equal(voicePillLabel({ status: "connecting" }), "Connecting");
  assert.equal(voicePillLabel({ status: "live" }), "Listening");
  assert.equal(voicePillLabel({ status: "live", speaker: "user" }), "Listening");
  assert.equal(voicePillLabel({ status: "live", busy: true }), "Thinking");
  assert.equal(voicePillLabel({ status: "live", busy: true, speaker: "assistant" }), "Speaking");
  assert.equal(voicePillLabel({ status: "error", speaker: "assistant" }), "Voice off");
  assert.equal(voicePillLabel({ status: "closing" }), "Ending");
});
