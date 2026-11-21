import assert from "node:assert/strict";
import test from "node:test";
import { WINDOW_SAMPLES, createTurnGate, createWindow, downsample, rms } from "../app/smart-turn-policy.mjs";

const FRAME = 40;

function run(gate, frames, model = () => 0) {
  const events = [];
  let now = 0;
  for (const frame of frames) {
    now += FRAME;
    for (const action of gate.frame({ now, ...frame })) {
      events.push({ now, ...action });
      if (action.type === "check") {
        for (const result of gate.modelResult(model(now))) events.push({ now, ...result });
      }
    }
  }
  return events;
}

const repeat = (count, frame) => Array.from({ length: count }, () => frame);
const speech = { micRms: 0.08, outputRms: 0 };
const silence = { micRms: 0.001, outputRms: 0 };

test("holds playback when the user starts speaking and releases on a confident model", () => {
  const events = run(createTurnGate({ confirmChecks: 1 }), [...repeat(10, speech), ...repeat(20, silence)], () => 0.9);
  assert.deepEqual(events.map((event) => event.type), ["hold", "check", "release"]);
  assert.equal(events.at(-1).reason, "model");
});

test("default gate needs two confident checks before releasing", () => {
  const events = run(createTurnGate(), [...repeat(10, speech), ...repeat(20, silence)], () => 0.9);
  assert.deepEqual(events.map((event) => event.type), ["hold", "check", "check", "release"]);
});

test("a confident check followed by speech does not count toward release", () => {
  const gate = createTurnGate();
  const events = run(gate, [...repeat(10, speech), ...repeat(6, silence), ...repeat(3, speech), ...repeat(6, silence)], (now) => (now < 700 ? 0.9 : 0.1));
  assert.ok(!events.some((event) => event.type === "release"));
});

test("a mid-sentence pause with a low probability keeps the turn open", () => {
  const gate = createTurnGate();
  const events = run(gate, [...repeat(10, speech), ...repeat(15, silence), ...repeat(10, speech)], () => 0.1);
  assert.ok(!events.some((event) => event.type === "release"));
  assert.equal(gate.inTurn, true);
});

test("silence timeout releases even if the model never says complete", () => {
  const gate = createTurnGate({ silenceTimeoutMs: 800 });
  const events = run(gate, [...repeat(10, speech), ...repeat(30, silence)], () => 0.1);
  const release = events.find((event) => event.type === "release");
  assert.equal(release.reason, "silence-timeout");
});

test("assistant echo alone does not count as barge-in", () => {
  const echo = { micRms: 0.02, outputRms: 0.2 };
  const events = run(createTurnGate(), repeat(50, echo));
  assert.deepEqual(events, []);
});

test("loud user speech over the assistant holds playback and asks it to yield", () => {
  const overlap = { micRms: 0.4, outputRms: 0.2 };
  const events = run(createTurnGate(), repeat(10, overlap));
  assert.deepEqual(events.map((event) => event.type), ["hold", "yield"]);
  assert.ok(events[0].now >= 200);
});

test("assistant starting during an open turn triggers one yield", () => {
  const talkingOver = { micRms: 0.001, outputRms: 0.2 };
  const events = run(createTurnGate(), [...repeat(5, speech), ...repeat(3, talkingOver), ...repeat(3, talkingOver)], () => 0.1);
  assert.equal(events.filter((event) => event.type === "yield").length, 1);
});

test("window is right-aligned and peak-normalised", () => {
  const window = createWindow();
  window.push(Float32Array.from([0.25, -0.5]));
  const snapshot = window.snapshot();
  assert.equal(snapshot.length, WINDOW_SAMPLES);
  assert.equal(snapshot[WINDOW_SAMPLES - 1], -1);
  assert.equal(snapshot[WINDOW_SAMPLES - 2], 0.5);
  assert.equal(snapshot[0], 0);
});

test("window keeps the most recent samples after wrapping", () => {
  const window = createWindow();
  window.push(new Float32Array(WINDOW_SAMPLES).fill(0.1));
  window.push(Float32Array.from([0.2]));
  const snapshot = window.snapshot();
  assert.equal(snapshot[WINDOW_SAMPLES - 1], 1);
  assert.ok(Math.abs(snapshot[0] - 0.5) < 1e-6);
});

test("downsample and rms", () => {
  assert.equal(downsample(new Float32Array(480), 48_000).length, 160);
  assert.ok(Math.abs(rms(Float32Array.from([1, -1])) - 1) < 1e-9);
});
