"use client";

import { ArrowsClockwise, Camera, CameraSlash, CircleNotch } from "@phosphor-icons/react";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { CAPTURE_FILE_NAME, CAPTURE_JPEG_QUALITY, cameraConstraints, cameraErrorKind, cameraPath, cameraReducer, cameraStatusText, captureSize, idleCamera, mirrorPreview, nextCamera, videoInputs, type CameraOption, type CameraPath, type CameraState } from "./camera-capture.mjs";
import { PHOTO_BETA_NOTE } from "./photo-attachment.mjs";
import { BetaTag } from "./ui/beta-tag";
import { InputDialog } from "./ui/input-dialog";

/** "webcam" where the file input's `capture` attribute would only open the file picker. */
export function detectCameraPath(): CameraPath {
  if (typeof navigator === "undefined") return "native";
  const nav = navigator as Navigator & { userAgentData?: { mobile?: boolean } };
  return cameraPath({
    userAgent: nav.userAgent,
    platform: nav.platform,
    maxTouchPoints: nav.maxTouchPoints,
    coarsePointer: typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse)").matches,
    mobileHint: nav.userAgentData?.mobile,
    hasGetUserMedia: typeof nav.mediaDevices?.getUserMedia === "function",
  });
}

async function cameraPermissionPending(): Promise<boolean> {
  try {
    const status = await navigator.permissions?.query({ name: "camera" as PermissionName });
    return status?.state === "prompt";
  } catch {
    return false;
  }
}

type Shot = { blob: Blob; url: string };

const secondary = "min-h-11 rounded-full border border-accent-border px-3 text-sm font-semibold text-ink-secondary transition hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus";
const primary = "flex min-h-11 items-center justify-center gap-1.5 rounded-full bg-accent px-3 text-sm font-semibold text-on-accent transition hover:bg-accent-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 aria-disabled:cursor-not-allowed aria-disabled:opacity-50";

/**
 * Webcam "Take photo" for desktops and laptops. The captured frame goes to
 * `onPhoto` as a JPEG File and then through the same prepare/upload pipeline
 * as a picked photo. Every camera track is stopped on close, on capture, on
 * unmount and while the tab is hidden. Video only, never audio.
 */
export function CameraDialog({ open, onClose, onPhoto, onChooseFiles }: { open: boolean; onClose: () => void; onPhoto: (file: File) => void; onChooseFiles: () => void }) {
  const [state, dispatch] = useReducer(cameraReducer, idleCamera as CameraState);
  const [cameras, setCameras] = useState<CameraOption[]>([]);
  const [deviceId, setDeviceId] = useState<string | null>(null);
  const [mirrored, setMirrored] = useState(true);
  const [shot, setShot] = useState<Shot | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const sessionRef = useRef(0);
  const stateRef = useRef(state);
  const deviceRef = useRef(deviceId);
  const shotRef = useRef(shot);
  const openRef = useRef(open);
  useEffect(() => { stateRef.current = state; deviceRef.current = deviceId; shotRef.current = shot; openRef.current = open; });
  const captureRef = useRef<HTMLButtonElement>(null);
  const useRefButton = useRef<HTMLButtonElement>(null);
  const filesRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement | null>(null);

  const stop = useCallback(() => {
    sessionRef.current += 1;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
  }, []);

  const clearShot = useCallback(() => {
    if (shotRef.current) URL.revokeObjectURL(shotRef.current.url);
    shotRef.current = null;
    setShot(null);
  }, []);

  const refreshCameras = useCallback(async () => {
    try {
      const devices = await navigator.mediaDevices?.enumerateDevices();
      setCameras(videoInputs(devices));
    } catch {
      setCameras([]);
    }
  }, []);

  const attachStream = useCallback((video: HTMLVideoElement | null) => {
    videoRef.current = video;
    const stream = streamRef.current;
    if (!video || !stream || video.srcObject === stream) return;
    video.srcObject = stream;
    void video.play().catch(() => undefined);
  }, []);

  const start = useCallback(async (requestedId?: string | null) => {
    stop();
    const session = sessionRef.current;
    const media = navigator.mediaDevices;
    dispatch({ type: "start", prompt: false });
    const prompt = await cameraPermissionPending();
    if (session !== sessionRef.current) return;
    if (prompt) dispatch({ type: "start", prompt: true });
    if (typeof media?.getUserMedia !== "function") {
      dispatch({ type: "failed", kind: "not_found" });
      return;
    }
    const request = (id?: string | null) => media.getUserMedia(cameraConstraints({ deviceId: id ?? undefined, facingMode: "user" }));
    let stream: MediaStream;
    try {
      // A remembered camera may have been unplugged; fall back to any camera once.
      stream = await request(requestedId).catch((error: unknown) => (requestedId && cameraErrorKind(error) === "not_found" ? request(null) : Promise.reject(error)));
    } catch (error) {
      if (session === sessionRef.current) dispatch({ type: "failed", kind: cameraErrorKind(error) });
      return;
    }
    if (session !== sessionRef.current) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    streamRef.current = stream;
    const track = stream.getVideoTracks()[0];
    const settings = track?.getSettings?.() ?? {};
    setDeviceId(settings.deviceId ?? requestedId ?? null);
    setMirrored(mirrorPreview({ facingMode: settings.facingMode, label: track?.label }));
    track?.addEventListener("ended", () => {
      if (streamRef.current !== stream) return;
      stop();
      dispatch({ type: "failed", kind: "unknown" });
    });
    attachStream(videoRef.current);
    void refreshCameras();
  }, [attachStream, refreshCameras, stop]);

  useEffect(() => {
    if (!open) return;
    void start();
    return () => {
      stop();
      clearShot();
      dispatch({ type: "reset" });
    };
  }, [open, start, stop, clearShot]);

  useEffect(() => {
    if (!open) return;
    const onVisibility = () => {
      const status = stateRef.current.status;
      if (document.hidden) {
        if (status === "starting" || status === "live") {
          stop();
          dispatch({ type: "paused" });
        }
      } else if (status === "paused") {
        void start(deviceRef.current);
      }
    };
    const onDeviceChange = () => void refreshCameras();
    document.addEventListener("visibilitychange", onVisibility);
    navigator.mediaDevices?.addEventListener?.("devicechange", onDeviceChange);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      navigator.mediaDevices?.removeEventListener?.("devicechange", onDeviceChange);
    };
  }, [open, refreshCameras, start, stop]);

  useEffect(() => {
    if (!open) return;
    const active = document.activeElement;
    const focusLost = !active || active === document.body || active === panelRef.current;
    if (state.status === "captured") useRefButton.current?.focus();
    else if (state.status === "error") filesRef.current?.focus();
    else if ((state.status === "starting" || state.status === "live") && focusLost) captureRef.current?.focus();
  }, [open, state.status]);

  const capture = async () => {
    const video = videoRef.current;
    const stream = streamRef.current;
    if (stateRef.current.status !== "live" || !video || !stream) return;
    const size = captureSize(video.videoWidth, video.videoHeight, stream.getVideoTracks()[0]?.getSettings?.());
    if (!size.width) return;
    const canvas = document.createElement("canvas");
    canvas.width = size.width;
    canvas.height = size.height;
    const context = canvas.getContext("2d");
    if (!context) {
      dispatch({ type: "failed", kind: "unknown" });
      return;
    }
    // Drawn unmirrored: only the preview is flipped.
    context.drawImage(video, 0, 0, size.width, size.height);
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", CAPTURE_JPEG_QUALITY));
    stop();
    if (!openRef.current) return;
    if (!blob) {
      dispatch({ type: "failed", kind: "unknown" });
      return;
    }
    clearShot();
    const next = { blob, url: URL.createObjectURL(blob) };
    shotRef.current = next;
    setShot(next);
    dispatch({ type: "captured" });
  };

  const retake = () => {
    clearShot();
    void start(deviceRef.current);
  };

  const usePhoto = () => {
    const current = shotRef.current;
    if (!current) return;
    onPhoto(new File([current.blob], CAPTURE_FILE_NAME, { type: "image/jpeg" }));
    onClose();
  };

  const switchTo = nextCamera(cameras, deviceId);
  const live = state.status === "live";
  const captured = state.status === "captured" && shot;
  const error = state.status === "error" ? state.kind : null;
  const status = cameraStatusText(state);

  return (
    <InputDialog
      open={open}
      title="Take a photo"
      description={<><span className="mb-1 flex items-center gap-2"><BetaTag /><span>{PHOTO_BETA_NOTE}</span></span>Hold the product, list or screen up to your camera. GharSaathi reads it once, then deletes it.</>}
      onClose={onClose}
      initialFocusRef={captureRef}
      onShown={(dialog) => { panelRef.current = dialog; }}
      widthClassName="sm:max-w-xl"
      footer={({ requestClose }) => (
        <div className="grid grid-cols-[auto_1fr] gap-2 sm:grid-cols-[auto_auto_1fr]">
          <button type="button" onClick={() => requestClose("cancel")} className={`${secondary} px-4`}>Cancel</button>
          {captured ? <>
            <button type="button" onClick={retake} className={`${secondary} px-4 max-sm:col-start-2`}>Retake</button>
            <button ref={useRefButton} type="button" onClick={usePhoto} className={`${primary} max-sm:col-span-2`}>Use photo</button>
          </> : error ? <>
            {error !== "denied" ? <button type="button" onClick={() => void start(deviceRef.current)} className={`${secondary} px-4 max-sm:col-start-2`}>Try again</button> : <span className="hidden sm:block" aria-hidden="true" />}
            <button ref={filesRef} type="button" onClick={onChooseFiles} className={`${primary} ${error !== "denied" ? "max-sm:col-span-2" : ""}`}>Choose from files</button>
          </> : <>
            <span className="hidden sm:block" aria-hidden="true" />
            <button ref={captureRef} type="button" onClick={() => void capture()} aria-disabled={!live} className={primary}><Camera size={18} weight="fill" />Capture</button>
          </>}
        </div>
      )}
    >
      <div className="relative mx-auto aspect-[4/3] max-h-[56dvh] w-full overflow-hidden rounded-2xl bg-inverse">
        <video
          ref={attachStream}
          autoPlay
          muted
          playsInline
          aria-label="Camera preview"
          onLoadedMetadata={() => dispatch({ type: "live" })}
          className={`size-full object-contain ${mirrored ? "-scale-x-100" : ""} ${live ? "opacity-100" : "opacity-0"} ${captured ? "hidden" : ""}`}
        />
        {captured && (
          // eslint-disable-next-line @next/next/no-img-element -- local blob preview of the captured frame
          <img src={shot.url} alt="Captured photo" className="absolute inset-0 size-full object-contain" />
        )}
        {!live && !captured && (
          <div className="absolute inset-0 grid place-items-center p-6 text-center text-on-inverse" aria-hidden="true">
            {error ? <CameraSlash size={34} /> : <CircleNotch size={30} className="motion-safe:animate-spin" />}
          </div>
        )}
        {live && switchTo && (
          <button type="button" onClick={() => void start(switchTo.deviceId)} aria-label={`Switch camera to ${switchTo.label}`} title={`Switch to ${switchTo.label}`} className="absolute right-3 top-3 grid size-11 place-items-center rounded-full bg-inverse/70 text-on-inverse transition hover:bg-inverse focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-on-inverse"><ArrowsClockwise size={20} /></button>
        )}
      </div>
      <p role="status" aria-live="polite" className={`mt-3 text-sm leading-5 ${error ? "rounded-xl bg-danger-soft px-3 py-2 text-danger-hover" : "text-muted"}`}>{status}</p>
    </InputDialog>
  );
}
