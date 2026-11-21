// Offline turn-taking evaluation for the smart-turn options (B) using the same
// turn gate as the browser. Usage: node scripts/eval-smart-turn.mjs manifest.json out.json
// The manifest is produced by backend/evaluation/run_turn_taking.py.
import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import * as ort from "onnxruntime-web";
import { MODEL_SHA256, SAMPLE_RATE, WINDOW_SAMPLES, createTurnGate, createWindow, rms } from "../app/smart-turn-policy.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const MODEL_PATH = resolve(root, "public/models/smart_turn_hinglish_4s-39736bc2.onnx");
const FRAME = 640; // 40 ms at 16 kHz, close to a 2048-sample ScriptProcessor buffer at 48 kHz.
const FRAME_MS = (FRAME / SAMPLE_RATE) * 1000;

function readWav(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let offset = 12;
  let rate = 0;
  while (offset < bytes.length) {
    const id = bytes.toString("ascii", offset, offset + 4);
    const size = view.getUint32(offset + 4, true);
    if (id === "fmt ") rate = view.getUint32(offset + 12, true);
    if (id === "data") {
      if (rate !== SAMPLE_RATE) throw new Error(`Expected ${SAMPLE_RATE} Hz WAV, got ${rate}`);
      const samples = new Float32Array(size / 2);
      for (let index = 0; index < samples.length; index += 1) samples[index] = view.getInt16(offset + 8 + index * 2, true) / 32768;
      return samples;
    }
    offset += 8 + size + (size % 2);
  }
  throw new Error("WAV has no data chunk");
}

const load = async (path) => readWav(await readFile(path));
const gain = (db) => 10 ** (db / 20);
const percentile = (values, p) => {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))];
};
const round = (value) => (value == null ? null : Math.round(value));

async function loadModel() {
  const bytes = await readFile(MODEL_PATH);
  const digest = createHash("sha256").update(bytes).digest("hex");
  if (digest !== MODEL_SHA256) throw new Error(`Model hash mismatch: ${digest}`);
  ort.env.wasm.numThreads = 1;
  const session = await ort.InferenceSession.create(new Uint8Array(bytes), { executionProviders: ["wasm"] });
  const timings = [];
  return {
    timings,
    async run(window) {
      const started = performance.now();
      const outputs = await session.run({ waveform: new ort.Tensor("float32", window, [1, WINDOW_SAMPLES]) });
      timings.push(performance.now() - started);
      return Number(outputs.p_complete.data[0]);
    },
  };
}

/** Streams a mic signal through the gate. `echo(frameIndex, held)` returns the echo residual frame. */
async function simulate({ mic, output = null, echoGain = 0, model, options }) {
  const gate = createTurnGate(options);
  const window = createWindow();
  const frames = Math.floor(mic.length / FRAME);
  const events = [];
  const checks = [];
  let held = false;
  for (let index = 0; index < frames; index += 1) {
    const now = (index + 1) * FRAME_MS;
    const chunk = mic.slice(index * FRAME, (index + 1) * FRAME);
    let outputRms = 0;
    if (output) {
      const out = output.subarray(index * FRAME, (index + 1) * FRAME);
      outputRms = out.length ? rms(out) : 0;
      // Local playback is muted while held, so no echo reaches the mic then.
      if (!held) for (let sample = 0; sample < out.length; sample += 1) chunk[sample] += out[sample] * echoGain;
    }
    window.push(chunk);
    const pending = [...gate.frame({ now, micRms: rms(chunk), outputRms })];
    while (pending.length) {
      const action = pending.shift();
      if (action.type === "check") {
        const probability = await model.run(window.snapshot());
        checks.push({ now, probability });
        // The browser decides only after inference finishes.
        for (const result of gate.modelResult(probability)) events.push({ ...result, now: now + model.timings.at(-1) });
        if (events.at(-1)?.type === "release") held = false;
        continue;
      }
      if (action.type === "hold") held = true;
      if (action.type === "release") held = false;
      events.push({ now, ...action });
    }
  }
  return { events, checks };
}

/** Plain energy endpointer: end of turn after `silenceMs` of silence (offline proxy, not GPT-Live). */
function silenceEndpointer(mic, silenceMs, speechRms = 0.012) {
  const releases = [];
  let inTurn = false;
  let lastSpeech = 0;
  for (let index = 0; index * FRAME < mic.length; index += 1) {
    const now = (index + 1) * FRAME_MS;
    const speaking = rms(mic.subarray(index * FRAME, (index + 1) * FRAME)) >= speechRms;
    if (speaking) {
      inTurn = true;
      lastSpeech = now;
    } else if (inTurn && now - lastSpeech >= silenceMs) {
      inTurn = false;
      releases.push(now);
    }
  }
  return releases;
}

function scoreEndOfTurn(releaseTimes, clip) {
  const endMs = clip.speech_end_s * 1000;
  // A release before the user finished is a false end of turn (the assistant would cut in).
  const early = releaseTimes.filter((time) => time < endMs - 50);
  const final = releaseTimes.find((time) => time >= endMs - 50);
  const pauseHits = (clip.pauses ?? []).map(([start, end], index) => ({
    kind: clip.pause_kinds?.[index] ?? "unknown",
    cut: releaseTimes.some((time) => time >= start * 1000 && time < end * 1000 + 150),
  }));
  return { false_eot: early.length, cut_off: early.length > 0, latency_ms: final == null ? null : final - endMs, pause_hits: pauseHits };
}

function pauseRate(rows, kind) {
  const hits = rows.flatMap((row) => row.pause_hits ?? []).filter((hit) => hit.kind === kind);
  return `${hits.filter((hit) => hit.cut).length}/${hits.length}`;
}

function summarise(rows) {
  const eot = rows.filter((row) => row.kind !== "barge_in" && row.kind !== "echo_only");
  const latencies = eot.filter((row) => !row.cut_off && row.latency_ms != null).map((row) => row.latency_ms);
  return {
    clips: eot.length,
    cut_off_clips: eot.filter((row) => row.cut_off).length,
    false_eot_events: eot.reduce((sum, row) => sum + row.false_eot, 0),
    missed_eot: eot.filter((row) => row.latency_ms == null).length,
    hesitation_pauses_cut: pauseRate(eot, "hesitation"),
    clause_pauses_cut: pauseRate(eot, "clause"),
    latency_median_ms: round(percentile(latencies, 0.5)),
    latency_p90_ms: round(percentile(latencies, 0.9)),
  };
}

async function main() {
  const [manifestPath, outPath] = process.argv.slice(2);
  if (!manifestPath || !outPath) throw new Error("usage: eval-smart-turn.mjs manifest.json out.json");
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  const model = await loadModel();
  const variants = {
    "B thr=0.5 confirm=1": { completeThreshold: 0.5, confirmChecks: 1 },
    "B thr=0.7 confirm=1": { completeThreshold: 0.7, confirmChecks: 1 },
    "B thr=0.5 confirm=2": { completeThreshold: 0.5, confirmChecks: 2 },
    "B default thr=0.7 confirm=2": {},
  };
  const results = { variants: {}, baselines: {}, barge_in: [], model: { sha256: MODEL_SHA256 } };

  for (const [name, options] of Object.entries(variants)) {
    const rows = [];
    for (const clip of manifest.clips.filter((item) => item.kind === "pause" || item.kind === "complete")) {
      const mic = await load(clip.mic);
      const { events, checks } = await simulate({ mic, model, options });
      const releases = events.filter((event) => event.type === "release");
      const scored = scoreEndOfTurn(releases.map((event) => event.now), clip);
      rows.push({
        id: clip.id, kind: clip.kind, ...scored,
        release_reasons: releases.map((event) => event.reason),
        pause_probabilities: checks.filter((check) => check.now < clip.speech_end_s * 1000).map((check) => +check.probability.toFixed(3)),
        end_probabilities: checks.filter((check) => check.now >= clip.speech_end_s * 1000).map((check) => +check.probability.toFixed(3)),
      });
    }
    results.variants[name] = { summary: summarise(rows), rows };
  }

  for (const silenceMs of [500, 800, 1200]) {
    const rows = [];
    for (const clip of manifest.clips.filter((item) => item.kind === "pause" || item.kind === "complete")) {
      rows.push({ id: clip.id, kind: clip.kind, ...scoreEndOfTurn(silenceEndpointer(await load(clip.mic), silenceMs), clip) });
    }
    results.baselines[`energy silence ${silenceMs} ms (proxy)`] = { summary: summarise(rows), rows };
  }

  for (const clip of manifest.clips.filter((item) => item.kind === "barge_in" || item.kind === "echo_only")) {
    const assistant = await load(clip.assistant);
    const mic = new Float32Array(assistant.length);
    if (clip.user) {
      const user = await load(clip.user);
      const start = Math.round(clip.user_start_s * SAMPLE_RATE);
      for (let index = 0; index < user.length && start + index < mic.length; index += 1) mic[start + index] += user[index];
    }
    const { events } = await simulate({ mic, output: assistant, echoGain: gain(clip.echo_db), model, options: {} });
    const hold = events.find((event) => event.type === "hold");
    const yieldSent = events.some((event) => event.type === "yield");
    const startMs = clip.user ? clip.user_start_s * 1000 : null;
    results.barge_in.push({
      id: clip.id, kind: clip.kind, echo_db: clip.echo_db,
      playback_stopped: Boolean(hold) && (startMs == null || hold.now >= startMs - 50),
      false_barge_in: Boolean(hold) && (startMs == null || hold.now < startMs - 50),
      stop_latency_ms: hold && startMs != null ? round(hold.now - startMs) : null,
      yield_sent: yieldSent,
    });
  }

  results.model.inference_ms_median = +percentile(model.timings, 0.5).toFixed(1);
  results.model.inference_ms_p90 = +percentile(model.timings, 0.9).toFixed(1);
  results.model.runs = model.timings.length;
  await writeFile(outPath, JSON.stringify(results, null, 2));
  console.log(JSON.stringify({ variants: Object.fromEntries(Object.entries(results.variants).map(([key, value]) => [key, value.summary])), baselines: Object.fromEntries(Object.entries(results.baselines).map(([key, value]) => [key, value.summary])), barge_in: results.barge_in, model: results.model }, null, 2));
}

await main();
