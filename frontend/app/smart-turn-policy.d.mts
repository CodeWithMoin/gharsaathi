export const SAMPLE_RATE: number;
export const WINDOW_SAMPLES: number;
export const MODEL_SHA256: string;

export type TurnOptions = {
  completeThreshold: number;
  confirmChecks: number;
  silenceBeforeCheckMs: number;
  checkIntervalMs: number;
  silenceTimeoutMs: number;
  speechRms: number;
  minSpeechMs: number;
  bargeInRms: number;
  bargeInEchoRatio: number;
  bargeInMs: number;
  outputActiveRms: number;
};
export const DEFAULT_TURN_OPTIONS: Readonly<TurnOptions>;

export type TurnAction =
  | { type: "hold" }
  | { type: "yield" }
  | { type: "check" }
  | { type: "release"; reason: "model" | "silence-timeout"; probability: number };

export type TurnGate = {
  readonly options: TurnOptions;
  readonly inTurn: boolean;
  frame(input: { now: number; micRms: number; outputRms?: number }): TurnAction[];
  modelResult(probability: number): TurnAction[];
  modelFailed(): TurnAction[];
  reset(): void;
};

export function createTurnGate(overrides?: Partial<TurnOptions>): TurnGate;
export function rms(samples: ArrayLike<number>): number;
export function downsample(input: Float32Array, inputRate: number, outputRate?: number): Float32Array;
export function createWindow(): { push(chunk: Float32Array): void; snapshot(): Float32Array; clear(): void };
export function peakNormalise(samples: Float32Array): Float32Array;
export function hex(buffer: ArrayBuffer): string;
