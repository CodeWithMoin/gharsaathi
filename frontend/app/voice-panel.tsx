"use client";

import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight, Microphone, ShieldCheck, Stop, X } from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { mapRealtimeTranscriptEvent } from "./voice-transcript.mjs";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type VoiceStatus = "connecting" | "live" | "closing" | "error";
type VoiceProduct = { provider?: "swiggy_instamart" | "zepto"; provider_label?: string; product_id: string; sku_id: string; spin_id?: string; name: string; brand?: string; pack_size?: string; price: { mrp: number; offer_price: number }; available: boolean; image_url?: string; rating_value?: string };
type LiveEvent = { type: string; error?: { message?: string }; [key: string]: unknown };
export type VoiceTranscriptTurn = { id: string; role: "user" | "assistant"; content: string };
type ToolEvent =
  | { type: "tool.started"; name: string }
  | { type: "tool.results"; query: string; store: string; products: VoiceProduct[] }
  | { type: "tool.not_connected"; query: string }
  | { type: "tool.error"; message: string }
  | { type: "basket.updated"; basket: { item_count: number; item_total: string } }
  | { type: "basket.approval_requested"; approval: { payable: string }; quote: { eta_text?: string | null } }
  | { type: "session.finished" };

async function waitForIceGathering(peer: RTCPeerConnection): Promise<void> {
  if (peer.iceGatheringState === "complete") return;
  await new Promise<void>((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      peer.removeEventListener("icegatheringstatechange", onChange);
      reject(new Error("Timed out while preparing the voice connection."));
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

export function VoicePanel({ addressId, zeptoAddressId, onClose, onResults, onTranscriptTurn, onBasketTouched }: { addressId: string; zeptoAddressId: string; onClose: () => void; onResults: (query: string, products: VoiceProduct[]) => void; onTranscriptTurn: (turn: VoiceTranscriptTurn) => void; onBasketTouched?: () => void }) {
  const [status, setStatus] = useState<VoiceStatus>("connecting");
  const [message, setMessage] = useState("Connecting to GharSaathi…");
  const [searching, setSearching] = useState(false);
  const [hasResults, setHasResults] = useState(false);
  const [basketNotice, setBasketNotice] = useState("");
  const peerRef = useRef<RTCPeerConnection | null>(null);
  const channelRef = useRef<RTCDataChannel | null>(null);
  const micRef = useRef<MediaStream | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const eventsRef = useRef<EventSource | null>(null);
  const transcriptTurnsRef = useRef(new Map<string, VoiceTranscriptTurn>());
  const activeTranscriptRef = useRef<{ role: VoiceTranscriptTurn["role"]; key: string } | null>(null);
  const transcriptIdRef = useRef(0);
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
    if (audioRef.current) audioRef.current.srcObject = null;
  }, []);

  const handleTranscriptEvent = useCallback((event: LiveEvent) => {
    const transcript = mapRealtimeTranscriptEvent(event);
    if (!transcript) return false;
    const sourceKey = transcript.sourceId ? `${transcript.role}:${transcript.sourceId}` : null;
    let key = sourceKey;
    if (!key && activeTranscriptRef.current?.role === transcript.role) key = activeTranscriptRef.current.key;
    if (!key) {
      transcriptIdRef.current += 1;
      key = `${transcript.role}:local-${transcriptIdRef.current}`;
    }
    if (transcript.operation === "finish") {
      if (activeTranscriptRef.current?.key === key) activeTranscriptRef.current = null;
      return true;
    }
    activeTranscriptRef.current = { role: transcript.role, key };
    const previous = transcriptTurnsRef.current.get(key);
    const content = transcript.operation === "replace" ? transcript.text : `${previous?.content ?? ""}${transcript.text}`;
    if (!content.trim()) return true;
    if (!previous) transcriptIdRef.current += 1;
    const turn: VoiceTranscriptTurn = {
      id: previous?.id ?? `voice-${Date.now().toString(36)}-${transcriptIdRef.current}`,
      role: transcript.role,
      content,
    };
    transcriptTurnsRef.current.set(key, turn);
    onTranscriptTurn({ ...turn, content: content.trim() });
    return true;
  }, [onTranscriptTurn]);

  const listenForToolEvents = useCallback((sessionId: string) => {
    const source = new EventSource(`${API}/api/voice/sessions/${encodeURIComponent(sessionId)}/events`);
    eventsRef.current = source;
    source.addEventListener("message", ({ data }) => {
      try {
        const event = JSON.parse(data) as ToolEvent;
        if (event.type === "tool.started" && event.name === "search_products") {
          setSearching(true);
          setMessage("Connected stores par dekh rahi hoon…");
        } else if (event.type === "tool.started") {
          setBasketNotice("");
          setMessage("Basket update kar raha hoon…");
        } else if (event.type === "tool.results") {
          setSearching(false);
          setHasResults(true);
          setMessage("Options mil gaye. Main best choices bata raha hoon.");
          onResults(event.query, event.products);
        } else if (event.type === "tool.not_connected") {
          setSearching(false);
          setMessage("Ek store connect karke delivery address choose kijiye.");
        } else if (event.type === "tool.error") {
          setSearching(false);
          setMessage(event.message);
        } else if (event.type === "basket.updated") {
          const items = event.basket.item_count;
          setBasketNotice(`${items} ${items === 1 ? "item" : "items"} · ${event.basket.item_total}`);
          setMessage(items ? "Basket update ho gaya." : "Basket is now empty.");
          onBasketTouched?.();
        } else if (event.type === "basket.approval_requested") {
          setBasketNotice(`Final bill · ${event.approval.payable}`);
          setMessage("Final bill ready hai—screen par review kijiye.");
          onBasketTouched?.();
        } else if (event.type === "session.finished") {
          source.close();
        }
      } catch {
        // Ignore malformed provider events.
      }
    });
  }, [onBasketTouched, onResults]);

  const start = useCallback(async () => {
    const attempt = attemptRef.current + 1;
    attemptRef.current = attempt;
    cleanup();
    activeTranscriptRef.current = null;
    setStatus("connecting");
    setMessage("Connecting to GharSaathi…");
    try {
      const peer = new RTCPeerConnection();
      peerRef.current = peer;
      peer.addEventListener("track", (event) => {
        if (!audioRef.current) return;
        audioRef.current.srcObject = new MediaStream([event.track]);
        audioRef.current.play().catch(() => setMessage("Tap once more to enable audio playback."));
      });
      peer.addEventListener("connectionstatechange", () => {
        if (attempt === attemptRef.current && peer.connectionState === "failed") {
          attemptRef.current += 1;
          setStatus("error");
          setMessage("Voice connection dropped. Please try again.");
          cleanup();
        }
      });

      const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
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
          if (event.type === "session.started") {
            setStatus("live");
            setMessage("Listening—boliye kya chahiye.");
          } else if (handleTranscriptEvent(event)) {
            // The main chat renders and persists spoken turns.
          } else if (event.type === "error") {
            setStatus("error");
            setMessage(event.error?.message ?? "Voice session failed.");
          } else if (event.type === "session.closed") onClose();
        } catch {
          // Ignore malformed realtime events.
        }
      });

      await peer.setLocalDescription(await peer.createOffer());
      await waitForIceGathering(peer);
      if (attempt !== attemptRef.current) return;
      const sdp = peer.localDescription?.sdp;
      if (!sdp) throw new Error("Could not prepare the microphone connection.");
      const response = await fetch(`${API}/api/voice/session`, {
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
      if (!response.ok) throw new Error(payload && typeof payload.detail === "string" ? payload.detail : "Could not start GharSaathi voice.");
      await peer.setRemoteDescription({ type: "answer", sdp: payload.sdp });
      listenForToolEvents(payload.session_id);
    } catch (error) {
      if (attempt !== attemptRef.current) return;
      const denied = error instanceof DOMException && error.name === "NotAllowedError";
      setStatus("error");
      setMessage(denied ? "Microphone permission is needed for voice shopping." : error instanceof Error ? error.message : "Could not start voice.");
      cleanup();
    }
  }, [addressId, cleanup, handleTranscriptEvent, listenForToolEvents, onClose, zeptoAddressId]);

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

  const statusLabel = status === "connecting" ? "Starting voice…" : status === "error" ? "Voice unavailable" : searching ? "Searching stores…" : "Listening…";

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
        <p className={`mt-0.5 truncate text-xs ${status === "error" ? "text-[#a44739]" : "text-[#806f65]"}`}>{message}</p>
      </div>
      <button onClick={closeConversation} aria-label="Close voice conversation" className="grid size-9 shrink-0 place-items-center rounded-full text-[#8c776b] transition hover:bg-[#f3e7dd] hover:text-[#3b2a22] active:scale-[.96]"><X size={18} /></button>
    </div>

    <AnimatePresence initial={false}>
      {basketNotice && <motion.p initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="mt-3 rounded-xl bg-[#f7e8dc] px-3 py-2 text-xs font-medium text-[#75422d]">{basketNotice}</motion.p>}
    </AnimatePresence>

    <div className="mt-3 flex items-center gap-2 border-t border-[#eee2d9] pt-3">
      <p className="mr-auto flex min-w-0 items-center gap-1.5 truncate text-[9px] text-[#8d7a6f]"><ShieldCheck size={13} className="shrink-0" /> Voice live while open · transcript saved</p>
      {status === "error" ? <button onClick={() => void start()} className="flex items-center gap-1.5 rounded-full bg-[#3b2a22] px-4 py-2.5 text-xs font-semibold text-white transition active:scale-[.97]">Try again <ArrowRight size={14} /></button> : hasResults ? <button onClick={closeConversation} className="flex items-center gap-1.5 rounded-full bg-[#3b2a22] px-4 py-2.5 text-xs font-semibold text-white transition active:scale-[.97]">See results <ArrowRight size={14} /></button> : <button onClick={closeConversation} className="flex items-center gap-2 rounded-full bg-[#c6531d] px-4 py-2.5 text-xs font-semibold text-white shadow-[0_8px_22px_-12px_rgba(158,65,24,.8)] transition hover:bg-[#b94a18] active:scale-[.97]"><Stop size={13} weight="fill" /> Stop</button>}
    </div>
    <audio ref={audioRef} autoPlay hidden />
  </motion.section>;
}
