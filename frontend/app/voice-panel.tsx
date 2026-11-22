"use client";

import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight, Microphone, ShieldCheck, Stop, X } from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { mapRealtimeBoundaryEvent, mapRealtimeTranscriptEvent } from "./voice-transcript.mjs";
import { createTranscriptTurnTracker } from "./voice-transcript-turns.mjs";
import { createVoiceEventRecorder, voiceEventLogEnabled } from "./voice-event-recorder.mjs";
import type { ApprovalRequest } from "./basket/BasketPanel";
import { API_BASE_URL, useAuthenticatedFetch } from "./authenticated-api";
import { applyAssistantEvent, type AssistantAddressFormRequest } from "./assistant-events.mjs";
import { SmartTurnController, loadSmartTurnModel } from "./smart-turn";

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

async function waitForIceGathering(peer: RTCPeerConnection): Promise<void> {
  if (peer.iceGatheringState === "complete") return;
  await new Promise<void>((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      peer.removeEventListener("icegatheringstatechange", onChange);
      reject(new Error("Voice connection taiyaar hone mein der ho gayi. Phir se try kijiye."));
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

export function VoicePanel({ addressId, zeptoAddressId, onClose, onResults, onTranscriptTurn, onBasketTouched, onApprovalRequested, onAddressForm }: { addressId: string; zeptoAddressId: string; onClose: () => void; onResults: (query: string, products: VoiceProduct[], item: { itemKey?: string; itemLabel?: string }) => void; onTranscriptTurn: (turn: VoiceTranscriptTurn) => void; onBasketTouched?: (provider?: VoiceStore) => void; onApprovalRequested?: (provider: VoiceStore, request: ApprovalRequest) => void; onAddressForm?: (request: VoiceAddressFormRequest) => void }) {
  const authenticatedFetch = useAuthenticatedFetch();
  const [status, setStatus] = useState<VoiceStatus>("connecting");
  const [message, setMessage] = useState("Voice connect ho raha hai…");
  const [searching, setSearching] = useState(false);
  const [hasResults, setHasResults] = useState(false);
  const [basketNotice, setBasketNotice] = useState("");
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
    const turn = transcriptTurnsRef.current.apply(transcript);
    if (turn) onTranscriptTurn({ ...turn, captionScript: assistantScriptRef.current });
    return true;
  }, [onTranscriptTurn]);

  const listenForToolEvents = useCallback((sessionId: string, eventsToken: string) => {
    const source = new EventSource(`${API_BASE_URL}/api/voice/sessions/${encodeURIComponent(sessionId)}/events?token=${encodeURIComponent(eventsToken)}`);
    eventsRef.current = source;
    source.addEventListener("message", ({ data }) => {
      try {
        const event = JSON.parse(data) as SessionEvent;
        eventRecorderRef.current?.record(event, "tools");
        if (event.type === "tool.started" && event.name === "search_products") {
          setSearching(true);
          setMessage("Connected stores par dekh rahi hoon…");
        } else if (event.type === "tool.started" && event.name === "open_address_form") {
          setMessage("Address form khol rahi hoon…");
        } else if (event.type === "tool.started") {
          setBasketNotice("");
          setMessage("Basket update kar rahi hoon…");
        } else if (event.type === "session.finished") {
          source.close();
        } else {
          const outcome = applyAssistantEvent<VoiceProduct>(event, { onResults, onBasketTouched, onApprovalRequested, onAddressForm });
          if (!outcome) return;
          if (outcome.kind === "results" || outcome.kind === "not_connected" || outcome.kind === "error") setSearching(false);
          if (outcome.kind === "results") setHasResults(true);
          if (outcome.notice) setBasketNotice(outcome.notice);
          setMessage(outcome.message);
        }
      } catch {
        // Ignore malformed provider events.
      }
    });
  }, [onAddressForm, onApprovalRequested, onBasketTouched, onResults]);

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
      setMessage("Smart listening load nahi hui—main normal tarah sun rahi hoon.");
    }
  }, []);

  const start = useCallback(async () => {
    const attempt = attemptRef.current + 1;
    attemptRef.current = attempt;
    cleanup();
    transcriptTurnsRef.current.reset();
    eventRecorderRef.current ??= voiceEventRecorder();
    setStatus("connecting");
    setMessage("Voice connect ho raha hai…");
    try {
      const peer = new RTCPeerConnection();
      peerRef.current = peer;
      peer.addEventListener("track", (event) => {
        if (!audioRef.current) return;
        const assistantStream = new MediaStream([event.track]);
        assistantStreamRef.current = assistantStream;
        smartTurnRef.current?.attachAssistant(assistantStream);
        audioRef.current.srcObject = assistantStream;
        audioRef.current.play().catch(() => setMessage("Awaaz sunne ke liye ek baar tap kijiye."));
      });
      peer.addEventListener("connectionstatechange", () => {
        if (attempt === attemptRef.current && peer.connectionState === "failed") {
          attemptRef.current += 1;
          setStatus("error");
          setMessage("Voice connection toot gaya. Phir se try kijiye.");
          cleanup();
        }
      });

      const mic = await navigator.mediaDevices.getUserMedia({ audio: MIC_CONSTRAINTS });
      if (attempt !== attemptRef.current) {
        mic.getTracks().forEach((track) => track.stop());
        peer.close();
        return;
      }
      micRef.current = mic;
      mic.getAudioTracks().forEach((track) => peer.addTrack(track, mic));
      const channel = peer.createDataChannel("oai-events");
      channelRef.current = channel;
      channel.addEventListener("message", ({ data }) => {
        if (attempt !== attemptRef.current) return;
        try {
          const event = JSON.parse(data) as LiveEvent;
          eventRecorderRef.current?.record(event, "datachannel");
          if (event.type === "session.started") {
            setStatus("live");
            setMessage("Listening—boliye kya chahiye.");
          } else if (handleTranscriptEvent(event)) {
            // The main chat renders and persists spoken turns.
          } else if (event.type === "error" && event.error?.client_event_id?.startsWith(YIELD_EVENT_PREFIX)) {
            // A rejected yield request is not fatal; GPT-Live keeps its native turn-taking.
          } else if (event.type === "error") {
            setStatus("error");
            setMessage("Voice session ruk gaya. Phir se try kijiye.");
            console.warn("[voice] session error", event.error?.message);
          } else if (event.type === "session.closed") onClose();
        } catch {
          // Ignore malformed realtime events.
        }
      });

      await peer.setLocalDescription(await peer.createOffer());
      await waitForIceGathering(peer);
      if (attempt !== attemptRef.current) return;
      const sdp = peer.localDescription?.sdp;
      if (!sdp) throw new Error("Mic connection taiyaar nahi ho paya. Phir se try kijiye.");
      const response = await authenticatedFetch(`${API_BASE_URL}/api/voice/session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sdp,
          swiggy_address_id: addressId || null,
          zepto_address_id: zeptoAddressId || null,
        }),
      });
      const payload = await response.json().catch(() => null);
      if (attempt !== attemptRef.current) return;
      if (!response.ok) throw new Error(payload && typeof payload.detail === "string" ? payload.detail : "GharSaathi voice shuru nahi ho paya. Phir se try kijiye.");
      await peer.setRemoteDescription({ type: "answer", sdp: payload.sdp });
      assistantScriptRef.current = payload.assistant_transcript_script === "native" ? "native" : "roman";
      listenForToolEvents(payload.session_id, payload.events_token);
      if (payload.turn_taking?.mode === "smart_turn" && typeof payload.turn_taking.yield_instruction === "string") {
        void startSmartTurn(attempt, mic, payload.turn_taking.yield_instruction);
      }
    } catch (error) {
      if (attempt !== attemptRef.current) return;
      const denied = error instanceof DOMException && error.name === "NotAllowedError";
      setStatus("error");
      setMessage(denied ? "Voice shopping ke liye mic permission chahiye." : error instanceof Error ? error.message : "Voice shuru nahi ho paya. Phir se try kijiye.");
      cleanup();
    }
  }, [addressId, authenticatedFetch, cleanup, handleTranscriptEvent, listenForToolEvents, onClose, startSmartTurn, zeptoAddressId]);

  useEffect(() => {
    void start();
    return () => {
      attemptRef.current += 1;
      cleanup();
    };
  }, [cleanup, start]);

  function closeConversation() {
    attemptRef.current += 1;
    setStatus("closing");
    if (channelRef.current?.readyState === "open") channelRef.current.send(JSON.stringify({ type: "session.close" }));
    cleanup();
    onClose();
  }

  const statusLabel = status === "connecting" ? "Voice shuru ho raha hai…" : status === "error" ? "Voice abhi band hai" : searching ? "Stores mein dekh rahi hoon…" : "Sun rahi hoon…";

  return <motion.section initial={{ opacity: 0, y: 12, scale: .985 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 8, scale: .99 }} transition={{ duration: .2 }} className="mx-auto max-w-3xl rounded-[26px] border border-[#deccbf] bg-[#fffdf9] p-3 shadow-[0_24px_60px_-28px_rgba(70,40,24,.55),inset_0_1px_0_rgba(255,255,255,.9)]" aria-label="Voice conversation">
    <div className="flex items-center gap-3">
      <div className="grid size-11 shrink-0 place-items-center rounded-[16px] bg-[#f7e8dc] text-[#c6531d]">
        <Microphone size={20} weight={status === "live" ? "fill" : "regular"} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className={`size-2 rounded-full ${status === "live" ? "bg-[#4d945d]" : status === "error" ? "bg-[#b65745]" : "animate-pulse bg-[#d68b60]"}`} />
          <strong className="truncate text-sm font-semibold text-[#3b2a22]">{statusLabel}</strong>
          {status === "live" && <div className="ml-auto flex h-5 items-center gap-1" aria-hidden="true">{Array.from({ length: 7 }, (_, index) => <motion.i key={index} animate={{ scaleY: [.35, 1, .35] }} transition={{ duration: .72, repeat: Infinity, delay: index * .07 }} style={{ height: `${7 + ((index * 5) % 12)}px` }} className="block w-0.5 origin-center rounded-full bg-[#c6531d]" />)}</div>}
        </div>
        <p className={`mt-0.5 line-clamp-2 text-xs leading-4 ${status === "error" ? "text-[#a44739]" : "text-[#75645a]"}`} role="status" aria-live="polite">{message}</p>
      </div>
      <button onClick={closeConversation} aria-label="Close voice conversation" className="grid size-9 shrink-0 place-items-center rounded-full text-[#8c776b] transition hover:bg-[#f3e7dd] hover:text-[#3b2a22] active:scale-[.96]"><X size={18} /></button>
    </div>

    <AnimatePresence initial={false}>
      {basketNotice && <motion.p initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="mt-3 rounded-xl bg-[#f7e8dc] px-3 py-2 text-xs font-medium text-[#75422d]">{basketNotice}</motion.p>}
    </AnimatePresence>

    <div className="mt-3 flex items-center gap-2 border-t border-[#eee2d9] pt-3">
      <p className="mr-auto flex min-w-0 items-center gap-1.5 text-[10px] leading-4 text-[#75645a]"><ShieldCheck size={13} className="shrink-0" /> <span className="truncate">Panel khula hai tab tak mic on · chat save hoti hai</span></p>
      {status === "error" ? <button onClick={() => void start()} className="flex items-center gap-1.5 rounded-full bg-[#3b2a22] px-4 py-2.5 text-xs font-semibold text-white transition active:scale-[.97]">Phir try karein <ArrowRight size={14} /></button> : hasResults ? <button onClick={closeConversation} className="flex items-center gap-1.5 rounded-full bg-[#3b2a22] px-4 py-2.5 text-xs font-semibold text-white transition active:scale-[.97]">Results dekhein <ArrowRight size={14} /></button> : <button onClick={closeConversation} className="flex items-center gap-2 rounded-full bg-[#c6531d] px-4 py-2.5 text-xs font-semibold text-white shadow-[0_8px_22px_-12px_rgba(158,65,24,.8)] transition hover:bg-[#b94a18] active:scale-[.97]"><Stop size={13} weight="fill" /> Band karein</button>}
    </div>
    <audio ref={audioRef} autoPlay hidden />
  </motion.section>;
}
