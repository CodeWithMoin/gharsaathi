"use client";

import type { InferenceSession } from "onnxruntime-web";
import {
  MODEL_SHA256,
  SAMPLE_RATE,
  WINDOW_SAMPLES,
  createTurnGate,
  createWindow,
  downsample,
  hex,
  rms,
  type TurnAction,
  type TurnGate,
} from "./smart-turn-policy.mjs";

const MODEL_URL = process.env.NEXT_PUBLIC_SMART_TURN_MODEL_URL ?? "/models/smart_turn_hinglish_4s-39736bc2.onnx";
const ORT_BASE_URL = (process.env.NEXT_PUBLIC_ORT_WASM_BASE_URL ?? "/ort/").replace(/\/?$/, "/");

export type SmartTurnModel = { run(window: Float32Array): Promise<number> };

let modelPromise: Promise<SmartTurnModel> | null = null;

/** Lazy-loads the self-hosted model once, verifying its SHA-256 before use. */
export function loadSmartTurnModel(): Promise<SmartTurnModel> {
  modelPromise ??= loadModel().catch((error) => {
    modelPromise = null;
    throw error;
  });
  return modelPromise;
}

async function loadModel(): Promise<SmartTurnModel> {
  const response = await fetch(MODEL_URL, { credentials: "same-origin" });
  if (!response.ok) throw new Error(`Smart-turn model unavailable (HTTP ${response.status}).`);
  const bytes = await response.arrayBuffer();
  if (!crypto?.subtle) throw new Error("Smart-turn model cannot be verified in this browser.");
  const digest = hex(await crypto.subtle.digest("SHA-256", bytes));
  if (digest !== MODEL_SHA256) throw new Error("Smart-turn model failed its integrity check.");
  const ort = await import("onnxruntime-web");
  ort.env.wasm.numThreads = 1;
  ort.env.wasm.wasmPaths = {
    mjs: `${ORT_BASE_URL}ort-wasm-simd-threaded.mjs`,
    wasm: `${ORT_BASE_URL}ort-wasm-simd-threaded.wasm`,
  };
  const session: InferenceSession = await ort.InferenceSession.create(new Uint8Array(bytes), {
    executionProviders: ["wasm"],
    graphOptimizationLevel: "all",
  });
  return {
    async run(window) {
      const outputs = await session.run({ waveform: new ort.Tensor("float32", window, [1, WINDOW_SAMPLES]) });
      return Number(outputs.p_complete?.data[0] ?? 0);
    },
  };
}

type Handlers = {
  /** Mute local playback: the user has the floor. */
  onHold: () => void;
  /** Resume local playback: the user's turn is complete. */
  onRelease: () => void;
  /** Ask GPT-Live to stop speaking (barge-in or speaking over an unfinished turn). */
  onYield: () => void;
};

/** Runs the turn gate on the processed (echo-cancelled) mic and the assistant's audio level. */
export class SmartTurnController {
  private readonly gate: TurnGate = createTurnGate();
  private readonly window = createWindow();
  private context: AudioContext | null = null;
  private processor: ScriptProcessorNode | null = null;
  private outputAnalyser: AnalyserNode | null = null;
  private outputBuffer: Float32Array<ArrayBuffer> | null = null;
  private stopped = false;

  constructor(private readonly model: SmartTurnModel, private readonly handlers: Handlers) {}

  async start(mic: MediaStream, assistant: MediaStream | null): Promise<void> {
    const context = new AudioContext();
    this.context = context;
    await context.resume();
    if (this.stopped) return;
    const source = context.createMediaStreamSource(mic);
    const processor = context.createScriptProcessor(2_048, 1, 1);
    const sink = context.createGain();
    sink.gain.value = 0;
    processor.onaudioprocess = (event) => this.accept(event.inputBuffer.getChannelData(0), event.inputBuffer.sampleRate);
    source.connect(processor);
    processor.connect(sink);
    sink.connect(context.destination);
    this.processor = processor;
    if (assistant) this.attachAssistant(assistant);
  }

  attachAssistant(stream: MediaStream): void {
    if (!this.context || this.stopped) return;
    const analyser = this.context.createAnalyser();
    analyser.fftSize = 1_024;
    this.context.createMediaStreamSource(stream).connect(analyser);
    this.outputAnalyser = analyser;
    this.outputBuffer = new Float32Array(analyser.fftSize);
  }

  stop(): void {
    this.stopped = true;
    if (this.processor) this.processor.onaudioprocess = null;
    void this.context?.close();
    this.context = null;
    this.processor = null;
    this.outputAnalyser = null;
  }

  private accept(input: Float32Array, rate: number): void {
    if (this.stopped) return;
    const chunk = downsample(input, rate, SAMPLE_RATE);
    this.window.push(chunk);
    let outputRms = 0;
    if (this.outputAnalyser && this.outputBuffer) {
      this.outputAnalyser.getFloatTimeDomainData(this.outputBuffer);
      outputRms = rms(this.outputBuffer);
    }
    this.apply(this.gate.frame({ now: performance.now(), micRms: rms(chunk), outputRms }));
  }

  private apply(actions: TurnAction[]): void {
    for (const action of actions) {
      if (action.type === "hold") this.handlers.onHold();
      else if (action.type === "yield") this.handlers.onYield();
      else if (action.type === "release") this.handlers.onRelease();
      else if (action.type === "check") void this.check();
    }
  }

  private async check(): Promise<void> {
    try {
      const probability = await this.model.run(this.window.snapshot());
      if (!this.stopped) this.apply(this.gate.modelResult(probability));
    } catch {
      this.gate.modelFailed();
    }
  }
}
