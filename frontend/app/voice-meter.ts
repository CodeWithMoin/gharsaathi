import { BAR_INTERVAL_MS, PILL_BAR_COUNT, createBarHistory, createSpeakerTracker, levelFromRms, rmsFromSamples, smoothLevel, type VoiceSpeaker } from "./voice-levels.mjs";

// Fallbacks only; the live values come from the --color-meter-* tokens in globals.css.
const FALLBACK_COLORS = { user: "rgb(59 42 34)", assistant: "rgb(198 83 29)", idle: "rgb(217 198 184)" } as const;
const COLOR_TOKENS = { user: "--color-meter-user", assistant: "--color-meter-assistant", idle: "--color-meter-idle" } as const;

type MeterColors = Record<keyof typeof FALLBACK_COLORS, string>;

function readMeterColors(element: Element): MeterColors {
  const style = getComputedStyle(element);
  const colors = { ...FALLBACK_COLORS } as MeterColors;
  for (const key of Object.keys(COLOR_TOKENS) as (keyof MeterColors)[]) {
    const channels = style.getPropertyValue(COLOR_TOKENS[key]).trim();
    if (channels) colors[key] = `rgb(${channels})`;
  }
  return colors;
}
const MIN_FRAME_MS = 1000 / 60;
const REDUCED_FRAME_MS = 1000 / 8;
const BAR_WIDTH = 2.5;
const BAR_GAP = 2;

type Kind = "user" | "assistant";

/**
 * Live level meter for the voice pill: one analyser on the mic and one on the
 * assistant's remote audio, drawn to a small canvas on requestAnimationFrame.
 * The analysers are never connected to the speakers. Drawing pauses while the
 * tab is hidden; with reduced motion it shows a static level bar instead of
 * the scrolling waveform.
 */
export class VoiceLevelMeter {
  private context: AudioContext | null = null;
  private readonly analysers: Partial<Record<Kind, { analyser: AnalyserNode; source: MediaStreamAudioSourceNode; stream: MediaStream }>> = {};
  private readonly samples = new Uint8Array(512);
  private readonly levels: Record<Kind, number> = { user: 0, assistant: 0 };
  private readonly history = createBarHistory(PILL_BAR_COUNT);
  private readonly tracker = createSpeakerTracker();
  private speaker: VoiceSpeaker = null;
  private canvas: HTMLCanvasElement | null = null;
  private colors: MeterColors = { ...FALLBACK_COLORS };
  private frame = 0;
  private lastFrame = 0;
  private lastBar = 0;
  private reducedMotion = false;
  private stopped = false;

  constructor(private readonly onSpeaker: (speaker: VoiceSpeaker) => void) {
    if (typeof document !== "undefined") document.addEventListener("visibilitychange", this.onVisibility);
  }

  attach(kind: Kind, stream: MediaStream): void {
    if (this.stopped || this.analysers[kind]?.stream === stream) return;
    try {
      this.context ??= new AudioContext();
      void this.context.resume().catch(() => undefined);
      this.detach(kind);
      const source = this.context.createMediaStreamSource(stream);
      const analyser = this.context.createAnalyser();
      analyser.fftSize = this.samples.length;
      analyser.smoothingTimeConstant = 0;
      source.connect(analyser);
      this.analysers[kind] = { analyser, source, stream };
      this.run();
    } catch {
      // No Web Audio: the pill keeps its state word and a quiet meter.
    }
  }

  setCanvas(canvas: HTMLCanvasElement | null, reducedMotion: boolean): void {
    this.canvas = canvas;
    if (canvas) this.colors = readMeterColors(canvas);
    this.reducedMotion = reducedMotion;
    this.run();
  }

  stop(): void {
    this.stopped = true;
    cancelAnimationFrame(this.frame);
    this.frame = 0;
    if (typeof document !== "undefined") document.removeEventListener("visibilitychange", this.onVisibility);
    this.detach("user");
    this.detach("assistant");
    void this.context?.close().catch(() => undefined);
    this.context = null;
  }

  private detach(kind: Kind): void {
    this.analysers[kind]?.source.disconnect();
    delete this.analysers[kind];
    this.levels[kind] = 0;
  }

  private readonly onVisibility = () => {
    if (document.hidden) {
      cancelAnimationFrame(this.frame);
      this.frame = 0;
    } else {
      this.run();
    }
  };

  private run(): void {
    if (this.stopped || this.frame || !this.canvas || (typeof document !== "undefined" && document.hidden)) return;
    this.lastFrame = performance.now();
    this.frame = requestAnimationFrame(this.tick);
  }

  private readonly tick = (now: number) => {
    this.frame = 0;
    if (this.stopped || !this.canvas) return;
    const elapsed = now - this.lastFrame;
    if (elapsed >= (this.reducedMotion ? REDUCED_FRAME_MS : MIN_FRAME_MS)) {
      this.lastFrame = now;
      for (const kind of ["user", "assistant"] as const) {
        const entry = this.analysers[kind];
        let target = 0;
        if (entry) {
          entry.analyser.getByteTimeDomainData(this.samples);
          target = levelFromRms(rmsFromSamples(this.samples));
        }
        this.levels[kind] = smoothLevel(this.levels[kind], target, elapsed);
      }
      const speaker = this.tracker.update(this.levels.user, this.levels.assistant, now);
      if (speaker !== this.speaker) {
        this.speaker = speaker;
        this.onSpeaker(speaker);
      }
      if (now - this.lastBar >= BAR_INTERVAL_MS) {
        this.lastBar = now;
        const loudest: Kind = this.levels.assistant >= this.levels.user ? "assistant" : "user";
        this.history.push(this.levels[loudest], speaker ?? (this.levels[loudest] > 0.05 ? loudest : null));
      }
      this.draw();
    }
    this.frame = requestAnimationFrame(this.tick);
  };

  private draw(): void {
    const canvas = this.canvas;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;
    const { colors } = this;
    const ratio = window.devicePixelRatio || 1;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
    }
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);

    if (this.reducedMotion) {
      const loudest: Kind = this.levels.assistant >= this.levels.user ? "assistant" : "user";
      const barHeight = 4;
      const top = (height - barHeight) / 2;
      context.fillStyle = colors.idle;
      roundedBar(context, 0, top, width, barHeight);
      context.fillStyle = colors[this.speaker ?? loudest];
      roundedBar(context, 0, top, Math.max(barHeight, width * this.levels[loudest]), barHeight);
      return;
    }

    const { bars } = this.history;
    const total = bars.length * (BAR_WIDTH + BAR_GAP) - BAR_GAP;
    let x = (width - total) / 2;
    for (const bar of bars) {
      const barHeight = Math.max(BAR_WIDTH, bar.level * height);
      context.fillStyle = bar.speaker ? colors[bar.speaker] : colors.idle;
      roundedBar(context, x, (height - barHeight) / 2, BAR_WIDTH, barHeight);
      x += BAR_WIDTH + BAR_GAP;
    }
  }
}

function roundedBar(context: CanvasRenderingContext2D, x: number, y: number, width: number, height: number): void {
  const radius = Math.min(width, height) / 2;
  context.beginPath();
  if (typeof context.roundRect === "function") context.roundRect(x, y, width, height, radius);
  else context.rect(x, y, width, height);
  context.fill();
}
