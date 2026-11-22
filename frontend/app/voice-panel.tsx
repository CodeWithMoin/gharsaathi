"use client";

import { AnimatePresence, MotionConfig, motion, useReducedMotion } from "framer-motion";
import { ArrowClockwise, CircleNotch, Microphone, ShieldCheck, SpeakerHigh, Stop, WarningCircle } from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { mapRealtimeBoundaryEvent, mapRealtimeTranscriptEvent } from "./voice-transcript.mjs";
import { createTranscriptTurnTracker } from "./voice-transcript-turns.mjs";
import { createVoiceEventRecorder, voiceEventLogEnabled } from "./voice-event-recorder.mjs";
import type { ApprovalRequest } from "./basket/BasketPanel";
import { API_BASE_URL, useAuthenticatedFetch } from "./authenticated-api";
import { applyAssistantEvent, type AssistantAddressFormRequest } from "./assistant-events.mjs";
import type { OrderConfirmation } from "./order-confirmation.mjs";
import { SmartTurnController, loadSmartTurnModel } from "./smart-turn";
import { CHAT_SAVE_WAIT_MS, VOICE_DATA_CHANNEL_ID, canRequestSessionBeforeMic, createStartTimer, createThrottle, microphonePermission, settleWithin } from "./voice-start.mjs";
import { voicePillLabel, type VoiceSpeaker } from "./voice-levels.mjs";
import { VoiceLevelMeter } from "./voice-meter";

type VoiceStatus = "connecting" | "live" | "closing" | "error";
type VoiceStore = "swiggy_instamart" | "zepto";
type VoiceProduct = { provider?: VoiceStore; provider_label?: string; product_id: string; sku_id: string; spin_id?: string; name: string; brand?: string; pack_size?: string; price: { mrp: number; offer_price: number }; available: boolean; image_url?: string; rating_value?: string };
type LiveEvent = { type: string; error?: { message?: string; client_event_id?: string }; [key: string]: unknown };
export type VoiceTranscriptTurn = { id: string; role: "user" | "assistant"; content: string; captionScript?: "roman" | "native" };
type SessionEvent = { type?: unknown; name?: unknown };

export type VoiceAddressFormRequest = AssistantAddressFormRequest;

const YIELD_EVENT_PREFIX = "smart_turn_yield_";
const MIC_CONSTRAINTS: MediaTrackConstraints = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };

declare global {
  interface Window { __gharsaathiVoiceEvents?: unknown[] }
}

function voiceEventRecorder() {
  const enabled = typeof window !== "undefined" && voiceEventLogEnabled({
    envFlag: process.env.NEXT_PUBLIC_VOICE_EVENT_LOG,
    production: process.env.NODE_ENV === "production",
    storage: window.localStorage,
  });
  if (enabled) window.__gharsaathiVoiceEvents ??= [];
  return createVoiceEventRecorder({
    enabled,
    sink: enabled ? window.__gharsaathiVoiceEvents : undefined,
    log: (record) => console.debug("[voice-event]", record),
  });
}

const NOTICE_MS = 3_600;
const BUSY_TIMEOUT_MS = 12_000;
const PREWARM_INTERVAL_MS = 20_000;
const prewarmAllowed = createThrottle(PREWARM_INTERVAL_MS);

/**
 * Hover/focus/press on the mic button: opens the backend's OpenAI connection
 * and the browser's connection to the backend before the tap. Creates no
 * session and asks for no permission.
 */
export function useVoicePrewarm() {
  const authenticatedFetch = useAuthenticatedFetch();
  return useCallback(() => {
    if (!prewarmAllowed()) return;
    void authenticatedFetch(`${API_BASE_URL}/api/voice/prewarm`, { method: "POST" }).catch(() => undefined);
  }, [authenticatedFetch]);
}

// The documented flow waits for complete ICE gathering: the session request
// carries one SDP and GPT-Live has no trickle ICE
// (https://developers.openai.com/api/docs/guides/voice-webrtc?api=live#understand-the-connection-sequence).
async function waitForIceGathering(peer: RTCPeerConnection): Promise<void> {
  if (peer.iceGatheringState === "complete") return;
  await new Promise<void>((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      peer.removeEventListener("icegatheringstatechange", onChange);
      reject(new Error("The voice connection took too long to start. Please try again."));
    }, 10_000);
    function onChange() {
      if (peer.iceGatheringState !== "complete") return;
      window.clearTimeout(timeout);
      peer.removeEventListener("icegatheringstatechange", onChange);
      resolve();
    }
    peer.addEventListener("icegatheringstatechange", onChange);
  });
}

export function VoicePanel({ addressId, zeptoAddressId, onClose, onResults, onTranscriptTurn, onBasketTouched, onApprovalRequested, onAddressForm, onAddressSelected, onOrderConfirmation, beforeSessionStart }: { addressId: string; zeptoAddressId: string; onClose: () => void; onResults: (query: string, products: VoiceProduct[], item: { itemKey?: string; itemLabel?: string }) => void; onTranscriptTurn: (turn: VoiceTranscriptTurn) => void; onBasketTouched?: (provider?: VoiceStore) => void; onApprovalRequested?: (provider: VoiceStore, request: ApprovalRequest) => void; onAddressForm?: (request: VoiceAddressFormRequest) => void | Promise<{ form_visible: boolean; reason?: string; filled_fields?: string[]; kept_user_fields?: string[] }>; onAddressSelected?: (selection: { provider: VoiceStore; addressId: string; label?: string; billCancelled: boolean }) => void; onOrderConfirmation?: (confirmation: OrderConfirmation) => void; beforeSessionStart?: () => Promise<void> }) {
  const authenticatedFetch = useAuthenticatedFetch();
  // Parent callbacks change identity on unrelated re-renders (e.g. commerceMode);
  // reading them through a ref keeps the live session from restarting.
  const callbacksRef = useRef({ onClose, onResults, onTranscriptTurn, onBasketTouched, onApprovalRequested, onAddressForm, onAddressSelected, onOrderConfirmation, beforeSessionStart, authenticatedFetch });
  // Addresses the server-side session is using. A switch made by the assistant
  // updates this first, so it does not restart the session; a manual change does.
  const latestAddressesRef = useRef({ swiggy: addressId, zepto: zeptoAddressId });
  latestAddressesRef.current = { swiggy: addressId, zepto: zeptoAddressId };
  const sessionAddressesRef = useRef<{ swiggy: string; zepto: string } | null>(null);
  useEffect(() => {
    callbacksRef.current = { onClose, onResults, onTranscriptTurn, onBasketTouched, onApprovalRequested, onAddressForm, onAddressSelected, onOrderConfirmation, beforeSessionStart, authenticatedFetch };
  });
  const [status, setStatus] = useState<VoiceStatus>("connecting");
  const [message, setMessage] = useState("Connecting voice…");
  const [busy, setBusy] = useState(false);
  const [basketNotice, setBasketNotice] = useState("");
  const [speaker, setSpeaker] = useState<VoiceSpeaker>(null);
  const [expanded, setExpanded] = useState(false);
  const [playBlocked, setPlayBlocked] = useState(false);
  const reduceMotion = useReducedMotion() ?? false;
  const meterRef = useRef<VoiceLevelMeter | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const smartTurnRef = useRef<SmartTurnController | null>(null);
  const assistantStreamRef = useRef<MediaStream | null>(null);
  const peerRef = useRef<RTCPeerConnection | null>(null);
  const channelRef = useRef<RTCDataChannel | null>(null);
  const micRef = useRef<MediaStream | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const eventsRef = useRef<EventSource | null>(null);
  const transcriptTurnsRef = useRef(createTranscriptTurnTracker());
  const assistantScriptRef = useRef<"roman" | "native">("roman");
  const eventRecorderRef = useRef<ReturnType<typeof voiceEventRecorder> | null>(null);
  const attemptRef = useRef(0);

  const cleanup = useCallback(() => {
    micRef.current?.getTracks().forEach((track) => track.stop());
    micRef.current = null;
    channelRef.current?.close();
    channelRef.current = null;
    peerRef.current?.close();
    peerRef.current = null;
    eventsRef.current?.close();
    eventsRef.current = null;
    smartTurnRef.current?.stop();
    smartTurnRef.current = null;
    meterRef.current?.stop();
    meterRef.current = null;
    assistantStreamRef.current = null;
    if (audioRef.current) audioRef.current.muted = false;
    if (audioRef.current) audioRef.current.srcObject = null;
  }, []);

  const handleTranscriptEvent = useCallback((event: LiveEvent) => {
    const boundary = mapRealtimeBoundaryEvent(event);
    // Boundaries also carry other meaning (e.g. session.closed), so keep dispatching them.
    if (boundary) transcriptTurnsRef.current.boundary(boundary);
    const transcript = mapRealtimeTranscriptEvent(event);
    if (!transcript) return false;
    // Tools without a UI event never report completion; her reply ends "Thinking".
    if (transcript.role === "assistant") setBusy(false);
    const turn = transcriptTurnsRef.current.apply(transcript);
    if (turn) callbacksRef.current.onTranscriptTurn({ ...turn, captionScript: assistantScriptRef.current });
    return true;
  }, []);

  const listenForToolEvents = useCallback((sessionId: string, eventsToken: string) => {
    const source = new EventSource(`${API_BASE_URL}/api/voice/sessions/${encodeURIComponent(sessionId)}/events?token=${encodeURIComponent(eventsToken)}`);
    eventsRef.current = source;
    source.addEventListener("message", ({ data }) => {
      try {
        const event = JSON.parse(data) as SessionEvent;
        eventRecorderRef.current?.record(event, "tools");
        if (event.type === "tool.started" && event.name === "search_products") {
          setBusy(true);
          setMessage("Searching your connected stores…");
        } else if (event.type === "tool.started" && event.name === "open_address_form") {
          setMessage("Opening the address form…");
        } else if (event.type === "tool.started") {
          setBusy(true);
          setBasketNotice("");
          setMessage("Updating your basket…");
        } else if (event.type === "session.finished") {
          source.close();
        } else {
          const selected = event as { type?: unknown; provider?: unknown; address_id?: unknown };
          if (selected.type === "address.selected" && typeof selected.address_id === "string" && sessionAddressesRef.current) {
            if (selected.provider === "zepto") sessionAddressesRef.current.zepto = selected.address_id;
            if (selected.provider === "swiggy_instamart") sessionAddressesRef.current.swiggy = selected.address_id;
          }
          const outcome = applyAssistantEvent<VoiceProduct>(event, callbacksRef.current);
          if (outcome?.ack && outcome.ackId) {
            // The backend waits for this before the assistant may say the form is open.
            void outcome.ack.then((ack) => callbacksRef.current.authenticatedFetch(`${API_BASE_URL}/api/voice/sessions/${encodeURIComponent(sessionId)}/ui-ack`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(ack),
            })).catch(() => undefined);
          }
          if (!outcome) return;
          setBusy(false);
          if (outcome.notice) setBasketNotice(outcome.notice);
          else if (outcome.kind === "results") setBasketNotice("Options are in the chat");
          setMessage(outcome.message);
        }
      } catch {
        // Ignore malformed provider events.
      }
    });
  }, []);

  const startSmartTurn = useCallback(async (attempt: number, mic: MediaStream, yieldInstruction: string) => {
    try {
      const model = await loadSmartTurnModel();
      if (attempt !== attemptRef.current) return;
      const controller = new SmartTurnController(model, {
        onHold: () => { if (audioRef.current) audioRef.current.muted = true; },
        onRelease: () => { if (audioRef.current) audioRef.current.muted = false; },
        onYield: () => {
          const channel = channelRef.current;
          if (channel?.readyState !== "open") return;
          channel.send(JSON.stringify({ type: "session.instructions.append", event_id: `${YIELD_EVENT_PREFIX}${Date.now()}`, delegation_id: null, content: yieldInstruction }));
        },
      });
      smartTurnRef.current = controller;
      await controller.start(mic, assistantStreamRef.current);
    } catch {
      if (attempt !== attemptRef.current) return;
      // Fall back to the tuned GPT-Live session (option A): no local gating.
      smartTurnRef.current?.stop();
      smartTurnRef.current = null;
      if (audioRef.current) audioRef.current.muted = false;
      setMessage("Smart listening didn't load—using standard listening.");
    }
  }, []);

  const reduceMotionRef = useRef(reduceMotion);
  useEffect(() => {
    reduceMotionRef.current = reduceMotion;
    meterRef.current?.setCanvas(canvasRef.current, reduceMotion);
  });

  const start = useCallback(async () => {
    const attempt = attemptRef.current + 1;
    attemptRef.current = attempt;
    const current = () => attempt === attemptRef.current;
    cleanup();
    sessionAddressesRef.current = { ...latestAddressesRef.current };
    transcriptTurnsRef.current.reset();
    eventRecorderRef.current ??= voiceEventRecorder();
    const timer = createStartTimer();
    setStatus("connecting");
    setMessage("Connecting voice…");
    setSpeaker(null);
    setBusy(false);
    setPlayBlocked(false);
    const meter = new VoiceLevelMeter((next) => { if (current()) setSpeaker(next); });
    meterRef.current = meter;
    meter.setCanvas(canvasRef.current, reduceMotionRef.current);
    // These run alongside the offer and ICE gathering instead of before them.
    // The server reads this chat's context at session start, so the pending save
    // gets a short head start, but never holds voice back for long.
    const chatSaved = settleWithin(callbacksRef.current.beforeSessionStart?.(), CHAT_SAVE_WAIT_MS);
    const permission = microphonePermission(typeof navigator === "undefined" ? undefined : navigator);
    let micError: unknown = null;
    const micReady = navigator.mediaDevices.getUserMedia({ audio: MIC_CONSTRAINTS }).then((stream) => {
      timer.mark("mic");
      if (!current()) stream.getTracks().forEach((track) => track.stop());
      return stream;
    });
    micReady.catch((error: unknown) => { micError = error; });
    try {
      const peer = new RTCPeerConnection();
      peerRef.current = peer;
      // The audio m-line is negotiated now; the mic track is swapped in when it opens.
      const transceiver = peer.addTransceiver("audio", { direction: "sendrecv" });
      const attachMic = async (mic: MediaStream) => {
        micRef.current = mic;
        await transceiver.sender.replaceTrack(mic.getAudioTracks()[0] ?? null);
        transceiver.sender.setStreams?.(mic);
        meter.attach("user", mic);
      };
      peer.addEventListener("track", (event) => {
        if (!audioRef.current) return;
        const assistantStream = new MediaStream([event.track]);
        assistantStreamRef.current = assistantStream;
        smartTurnRef.current?.attachAssistant(assistantStream);
        audioRef.current.srcObject = assistantStream;
        meter.attach("assistant", assistantStream);
        audioRef.current.play().catch(() => {
          setPlayBlocked(true);
          setMessage("Tap the voice control once to hear GharSaathi.");
        });
      });
      peer.addEventListener("connectionstatechange", () => {
        if (current() && peer.connectionState === "failed") {
          attemptRef.current += 1;
          setStatus("error");
          setMessage("The voice connection dropped. Please try again.");
          cleanup();
        }
      });

      const channel = peer.createDataChannel("oai-events", { negotiated: true, id: VOICE_DATA_CHANNEL_ID });
      channelRef.current = channel;
      channel.addEventListener("open", () => { timer.mark("channel_open"); }, { once: true });
      channel.addEventListener("message", ({ data }) => {
        if (!current()) return;
        try {
          const event = JSON.parse(data) as LiveEvent;
          eventRecorderRef.current?.record(event, "datachannel");
          if (event.type === "session.started") {
            timer.mark("session_started");
            eventRecorderRef.current?.recordTiming(timer.stages(), "client");
            setStatus("live");
            setMessage("Listening—say what you need.");
          } else if (handleTranscriptEvent(event)) {
            // The main chat renders and persists spoken turns.
          } else if (event.type === "error" && event.error?.client_event_id?.startsWith(YIELD_EVENT_PREFIX)) {
            // A rejected yield request is not fatal; GPT-Live keeps its native turn-taking.
          } else if (event.type === "error") {
            setStatus("error");
            setMessage("The voice session stopped. Please try again.");
            console.warn("[voice] session error", event.error?.message);
          } else if (event.type === "session.closed") callbacksRef.current.onClose();
        } catch {
          // Ignore malformed realtime events.
        }
      });

      await peer.setLocalDescription(await peer.createOffer());
      timer.mark("offer");
      await waitForIceGathering(peer);
      timer.mark("ice");
      if (!current()) return;
      const sdp = peer.localDescription?.sdp;
      if (!sdp) throw new Error("Could not set up the mic connection. Please try again.");
      let mic: MediaStream | null = null;
      // A session is billed once created, so with a permission prompt pending
      // the request waits for the user's answer.
      if (!canRequestSessionBeforeMic(await permission)) {
        mic = await micReady;
        if (!current()) return;
        await attachMic(mic);
      }
      if (micError) throw micError;
      await chatSaved;
      timer.mark("chat_saved");
      if (!current()) return;
      const response = await callbacksRef.current.authenticatedFetch(`${API_BASE_URL}/api/voice/session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sdp,
          swiggy_address_id: sessionAddressesRef.current.swiggy || null,
          zepto_address_id: sessionAddressesRef.current.zepto || null,
          dcid: VOICE_DATA_CHANNEL_ID,
        }),
      });
      const payload = await response.json().catch(() => null);
      timer.mark("session_created");
      if (!current()) return;
      if (!response.ok) throw new Error(payload && typeof payload.detail === "string" ? payload.detail : "Could not start GharSaathi voice. Please try again.");
      eventRecorderRef.current?.recordTiming(payload.start_timing_ms, "server");
      await peer.setRemoteDescription({ type: "answer", sdp: payload.sdp });
      timer.mark("answer_applied");
      assistantScriptRef.current = payload.assistant_transcript_script === "native" ? "native" : "roman";
      listenForToolEvents(payload.session_id, payload.events_token);
      if (!mic) {
        mic = await micReady;
        if (!current()) return;
        await attachMic(mic);
      }
      if (payload.turn_taking?.mode === "smart_turn" && typeof payload.turn_taking.yield_instruction === "string") {
        void startSmartTurn(attempt, mic, payload.turn_taking.yield_instruction);
      }
    } catch (error) {
      if (!current()) return;
      const denied = error instanceof DOMException && error.name === "NotAllowedError";
      setStatus("error");
      setMessage(denied ? "Voice shopping needs microphone permission." : error instanceof Error ? error.message : "Could not start voice. Please try again.");
      cleanup();
      void micReady.then((stream) => stream.getTracks().forEach((track) => track.stop()), () => undefined);
    }
  }, [cleanup, handleTranscriptEvent, listenForToolEvents, startSmartTurn]);

  useEffect(() => {
    void start();
    return () => {
      attemptRef.current += 1;
      cleanup();
    };
  }, [cleanup, start]);

  useEffect(() => {
    const session = sessionAddressesRef.current;
    if (session && (session.swiggy !== addressId || session.zepto !== zeptoAddressId)) void start();
  }, [addressId, start, zeptoAddressId]);

  useEffect(() => {
    if (!busy) return;
    const timer = window.setTimeout(() => setBusy(false), BUSY_TIMEOUT_MS);
    return () => window.clearTimeout(timer);
  }, [busy]);

  useEffect(() => {
    if (!basketNotice) return;
    const timer = window.setTimeout(() => setBasketNotice(""), NOTICE_MS);
    return () => window.clearTimeout(timer);
  }, [basketNotice]);

  function closeConversation() {
    attemptRef.current += 1;
    setStatus("closing");
    if (channelRef.current?.readyState === "open") channelRef.current.send(JSON.stringify({ type: "session.close" }));
    cleanup();
    onClose();
  }

  function playVoice() {
    audioRef.current?.play().then(() => {
      setPlayBlocked(false);
      setMessage("Listening—say what you need.");
    }).catch(() => undefined);
  }

  const label = voicePillLabel({ status, busy, speaker });
  const showDetails = expanded || status === "error" || playBlocked;
  const icon = status === "error" ? "error" : status === "connecting" || status === "closing" || (label === "Thinking") ? "wait" : speaker === "assistant" ? "speaker" : "mic";
  const iconTone = icon === "error" ? "bg-danger-soft text-danger" : icon === "speaker" ? "bg-accent text-on-accent" : icon === "mic" && speaker === "user" ? "bg-inverse text-on-inverse" : "bg-accent-soft text-accent";
  // MotionConfig below drops the transforms for reduced motion and keeps the fades.
  const swap = { initial: { opacity: 0, scale: .8, filter: "blur(2px)" }, animate: { opacity: 1, scale: 1, filter: "blur(0px)" }, exit: { opacity: 0, scale: .8, filter: "blur(2px)" } };
  const quick = { duration: .16, ease: [.23, 1, .32, 1] as const };
  const lift = { initial: { opacity: 0, y: 8, scale: .97 }, animate: { opacity: 1, y: 0, scale: 1 }, exit: { opacity: 0, y: 6, scale: .98 } };

  return <MotionConfig reducedMotion="user"><motion.section
    {...lift}
    transition={{ type: "spring", duration: .35, bounce: 0 }}
    className="mx-auto flex w-fit max-w-full flex-col items-center gap-2"
    aria-label="Voice conversation"
    role="region"
    onKeyDown={(event) => { if (event.key === "Escape" && expanded) { event.stopPropagation(); setExpanded(false); } }}
  >
    <p className="sr-only" role="status" aria-live="polite">{message}</p>

    <AnimatePresence initial={false}>
      {basketNotice && !showDetails && <motion.p key="notice" {...lift} transition={{ duration: .2, ease: [.23, 1, .32, 1] }} role="status" className="max-w-[min(88vw,340px)] truncate rounded-full bg-inverse px-3.5 py-2 text-xs font-medium text-on-inverse shadow-[0_14px_30px_-18px_rgb(var(--shadow)/.9)]">{basketNotice}</motion.p>}
    </AnimatePresence>

    <AnimatePresence initial={false}>
      {showDetails && <motion.div key="details" id="voice-details" {...lift} transition={{ duration: .2, ease: [.23, 1, .32, 1] }} style={{ transformOrigin: "bottom center" }} className="w-[min(88vw,340px)] rounded-[20px] border border-border bg-surface p-3.5 shadow-[0_20px_44px_-26px_rgb(var(--shadow)/.6)]">
        <p className={`text-[13px] leading-5 ${status === "error" ? "text-danger" : "text-ink-secondary"}`}>{message}</p>
        {basketNotice && <p className="mt-1.5 text-xs font-medium text-warning">{basketNotice}</p>}
        <div className="mt-2.5 flex items-center gap-3 text-[10px] leading-4 text-muted">
          <span className="flex items-center gap-1.5"><i className="size-1.5 rounded-full bg-meter-user" aria-hidden="true" />You</span>
          <span className="flex items-center gap-1.5"><i className="size-1.5 rounded-full bg-meter-assistant" aria-hidden="true" />GharSaathi</span>
          <span className="ml-auto flex min-w-0 items-center gap-1"><ShieldCheck size={12} className="shrink-0" /><span className="truncate">Mic is on until you stop · chat is saved</span></span>
        </div>
        {(status === "error" || playBlocked) && <div className="mt-3 flex gap-2">
          {status === "error" && <button type="button" onClick={() => void start()} className="flex items-center gap-1.5 rounded-full bg-inverse px-4 py-2 text-xs font-semibold text-on-inverse transition-transform duration-150 ease-out active:scale-[.97]"><ArrowClockwise size={14} /> Try again</button>}
          {playBlocked && status !== "error" && <button type="button" onClick={playVoice} className="flex items-center gap-1.5 rounded-full bg-accent px-4 py-2 text-xs font-semibold text-on-accent transition-transform duration-150 ease-out active:scale-[.97]"><SpeakerHigh size={14} weight="fill" /> Turn on sound</button>}
        </div>}
      </motion.div>}
    </AnimatePresence>

    <div className="flex items-center gap-1 rounded-full border border-border-strong bg-surface/90 p-1.5 shadow-[0_18px_40px_-22px_rgb(var(--shadow)/.6),inset_0_1px_0_rgba(255,255,255,.9)] backdrop-blur-xl [@media(prefers-reduced-transparency:reduce)]:bg-surface [@media(prefers-reduced-transparency:reduce)]:backdrop-blur-none">
      <button
        type="button"
        onClick={() => { if (playBlocked) playVoice(); setExpanded((open) => !open); }}
        aria-expanded={showDetails}
        aria-controls="voice-details"
        aria-label={`Voice: ${label}. ${showDetails ? "Hide" : "Show"} details`}
        className="flex items-center gap-2.5 rounded-full py-0.5 pl-0.5 pr-2 outline-none transition-transform duration-150 ease-out focus-visible:ring-2 focus-visible:ring-focus/50 active:scale-[.97]"
      >
        <span className={`relative grid size-9 shrink-0 place-items-center overflow-hidden rounded-full transition-colors duration-200 ${iconTone}`}>
          <AnimatePresence initial={false} mode="popLayout">
            <motion.span key={icon} {...swap} transition={quick} className="grid place-items-center">
              {icon === "error" ? <WarningCircle size={18} weight="fill" /> : icon === "wait" ? <CircleNotch size={18} weight="bold" className="animate-spin" /> : icon === "speaker" ? <SpeakerHigh size={18} weight="fill" /> : <Microphone size={18} weight="fill" />}
            </motion.span>
          </AnimatePresence>
        </span>
        <canvas ref={canvasRef} aria-hidden="true" className="h-[22px] w-[62px]" />
        <span className="relative grid w-[5.25rem] text-left text-sm font-semibold text-ink" aria-hidden="true">
          <AnimatePresence initial={false} mode="popLayout">
            <motion.span key={label} {...swap} transition={quick} className="col-start-1 row-start-1 origin-left">{label}</motion.span>
          </AnimatePresence>
        </span>
      </button>
      <button type="button" onClick={closeConversation} aria-label="End voice conversation" className="grid size-10 shrink-0 place-items-center rounded-full bg-accent text-on-accent shadow-[0_8px_20px_-12px_rgb(var(--accent-shadow)/.85)] outline-none transition-[transform,background-color] duration-150 ease-out hover:bg-accent-hover focus-visible:ring-2 focus-visible:ring-focus/50 focus-visible:ring-offset-2 active:scale-[.94]"><Stop size={15} weight="fill" /></button>
    </div>
    <audio ref={audioRef} autoPlay hidden />
  </motion.section></MotionConfig>;
}
