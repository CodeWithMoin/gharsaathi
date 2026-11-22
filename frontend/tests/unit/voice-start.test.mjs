import assert from "node:assert/strict";
import test from "node:test";
import { CHAT_SAVE_WAIT_MS, VOICE_DATA_CHANNEL_ID, canRequestSessionBeforeMic, createStartTimer, createThrottle, microphonePermission, settleWithin } from "../../app/voice-start.mjs";
import { createVoiceEventRecorder } from "../../app/voice-event-recorder.mjs";

test("the start timer records each stage once, in whole ms from the start", () => {
  let clock = 1000;
  const timer = createStartTimer(() => clock);
  clock = 1012.4;
  assert.equal(timer.mark("mic"), 12);
  clock = 1140;
  timer.mark("ice");
  timer.mark("mic");
  assert.deepEqual(timer.stages(), { mic: 12, ice: 140 });
});

test("the chat save gets a bounded wait and never rejects", async () => {
  const instant = (callback) => callback();
  assert.equal(await settleWithin(undefined, 150), "done");
  assert.equal(await settleWithin(Promise.resolve(), 150, () => undefined), "done");
  assert.equal(await settleWithin(Promise.reject(new Error("offline")), 150, () => undefined), "failed");
  assert.equal(await settleWithin(new Promise(() => undefined), 150, instant), "timeout");
  const started = Date.now();
  assert.equal(await settleWithin(new Promise(() => undefined), 30), "timeout");
  assert.ok(Date.now() - started < 200);
  assert.ok(CHAT_SAVE_WAIT_MS <= 150);
});

test("mic permission is read without prompting, and unknown when unsupported", async () => {
  const navigatorWith = (state) => ({ permissions: { query: async ({ name }) => { assert.equal(name, "microphone"); return { state }; } } });
  assert.equal(await microphonePermission(navigatorWith("granted")), "granted");
  assert.equal(await microphonePermission(navigatorWith("prompt")), "prompt");
  assert.equal(await microphonePermission(navigatorWith("weird")), "unknown");
  assert.equal(await microphonePermission({ permissions: { query: async () => { throw new TypeError("unsupported name"); } } }), "unknown");
  assert.equal(await microphonePermission({}), "unknown");
  assert.equal(await microphonePermission(undefined), "unknown");
});

test("a billed session is only requested before the mic opens when permission is granted", () => {
  assert.equal(canRequestSessionBeforeMic("granted"), true);
  for (const state of ["prompt", "denied", "unknown"]) assert.equal(canRequestSessionBeforeMic(state), false);
});

test("the prewarm throttle allows one call per interval", () => {
  let clock = 0;
  const allowed = createThrottle(20_000, () => clock);
  assert.equal(allowed(), true);
  clock = 5_000;
  assert.equal(allowed(), false);
  clock = 20_001;
  assert.equal(allowed(), true);
});

test("the pre-negotiated channel id is a valid SCTP stream id", () => {
  assert.ok(Number.isInteger(VOICE_DATA_CHANNEL_ID) && VOICE_DATA_CHANNEL_ID >= 0 && VOICE_DATA_CHANNEL_ID <= 65534);
});

test("start timings are recorded content-free and only when the log is on", () => {
  const sink = [];
  const recorder = createVoiceEventRecorder({ enabled: true, sink, now: () => 7 });
  recorder.recordTiming({ mic: 12.6, ice: 140, "Bad Key": 1, transcript: "मुझे AXE", nested: { a: 1 } }, "client");
  recorder.recordTiming(null, "server");
  assert.deepEqual(sink, [{ at: 7, source: "client", type: "voice.start_timing", ids: [], timing: { mic: 13, ice: 140 } }]);
  const off = [];
  createVoiceEventRecorder({ enabled: false, sink: off }).recordTiming({ mic: 1 }, "client");
  assert.deepEqual(off, []);
});
