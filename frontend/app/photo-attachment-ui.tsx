"use client";

import { Camera, CircleNotch, Image as ImageIcon, Paperclip, WarningCircle, X } from "@phosphor-icons/react";
import { type ChangeEvent, useCallback, useEffect, useReducer, useRef, useState } from "react";
import { API_BASE_URL, useAuthenticatedFetch } from "./authenticated-api";
import { CAMERA_ACCEPT, GALLERY_ACCEPT, JPEG_QUALITY, MAX_UPLOAD_BYTES, attachmentIdFor, downscaleSize, idlePhoto, isHeif, photoReducer, uploadErrorMessage, validatePhotoFile, type PhotoState } from "./photo-attachment.mjs";
import { CameraDialog, detectCameraPath } from "./camera-dialog";
import { InputDialog } from "./ui/input-dialog";

type Decoded = { source: CanvasImageSource; width: number; height: number; close: () => void };

async function decode(file: Blob): Promise<Decoded> {
  if (typeof createImageBitmap === "function") {
    try {
      const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
      return { source: bitmap, width: bitmap.width, height: bitmap.height, close: () => bitmap.close() };
    } catch {
      // Fall back to an <img>, which some browsers decode more formats with.
    }
  }
  const url = URL.createObjectURL(file);
  try {
    const image = new window.Image();
    image.decoding = "async";
    image.src = url;
    await image.decode();
    return { source: image, width: image.naturalWidth, height: image.naturalHeight, close: () => undefined };
  } finally {
    URL.revokeObjectURL(url);
  }
}

/**
 * Downscale to at most 1600 px and re-encode as JPEG. Drawing to a canvas keeps
 * only pixels, so EXIF (including GPS) is dropped before anything is uploaded.
 * HEIC that this browser cannot decode is sent as-is; the server strips it too.
 */
export async function preparePhoto(file: File): Promise<Blob> {
  let decoded: Decoded;
  try {
    decoded = await decode(file);
  } catch {
    if (isHeif(file) && file.size <= MAX_UPLOAD_BYTES) return file;
    throw new Error("That photo couldn't be opened. Try another one.");
  }
  try {
    const size = downscaleSize(decoded.width, decoded.height);
    const canvas = document.createElement("canvas");
    canvas.width = size.width;
    canvas.height = size.height;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("That photo couldn't be prepared.");
    context.drawImage(decoded.source, 0, 0, size.width, size.height);
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY));
    if (!blob) throw new Error("That photo couldn't be prepared.");
    return blob;
  } finally {
    decoded.close();
  }
}

/** One attachment at a time: pick → prepare → upload → ready; remove deletes it on the server. */
export function usePhotoAttachment() {
  const authenticatedFetch = useAuthenticatedFetch();
  const [state, dispatch] = useReducer(photoReducer, idlePhoto as PhotoState);
  const tokenRef = useRef(0);
  const stateRef = useRef<PhotoState>(state);
  useEffect(() => { stateRef.current = state; }, [state]);
  const previewRef = useRef<string | null>(null);
  const setPreview = (url: string | null) => {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    previewRef.current = url;
  };
  useEffect(() => () => setPreview(null), []);

  const discardOnServer = useCallback((attachmentId: string | null) => {
    if (!attachmentId) return;
    void authenticatedFetch(`${API_BASE_URL}/api/chat/attachments/${encodeURIComponent(attachmentId)}`, { method: "DELETE" }).catch(() => undefined);
  }, [authenticatedFetch]);

  const attach = useCallback(async (file: File | null | undefined) => {
    const token = ++tokenRef.current;
    discardOnServer(attachmentIdFor(stateRef.current));
    setPreview(null);
    const problem = validatePhotoFile(file);
    if (problem || !file) {
      dispatch({ type: "rejected", token, message: problem ?? "No photo was chosen." });
      return;
    }
    dispatch({ type: "start", token, previewUrl: null });
    try {
      const blob = await preparePhoto(file);
      if (token !== tokenRef.current) return;
      const previewUrl = isHeif(file) && blob === file ? null : URL.createObjectURL(blob);
      setPreview(previewUrl);
      dispatch({ type: "start", token, previewUrl });
      const body = new FormData();
      body.append("file", blob, blob === file ? file.name || "photo.heic" : "photo.jpg");
      const response = await authenticatedFetch(`${API_BASE_URL}/api/chat/attachments`, { method: "POST", body, cache: "no-store" });
      if (!response.ok) {
        dispatch({ type: "failed", token, message: uploadErrorMessage(response.status) });
        return;
      }
      const data = await response.json() as { attachment_id?: unknown };
      if (typeof data.attachment_id !== "string") throw new Error("The photo couldn't be uploaded. Try again.");
      if (token !== tokenRef.current) {
        discardOnServer(data.attachment_id);
        return;
      }
      dispatch({ type: "uploaded", token, attachmentId: data.attachment_id });
    } catch (cause) {
      dispatch({ type: "failed", token, message: cause instanceof Error && cause.message ? cause.message : "The photo couldn't be uploaded. Try again." });
    }
  }, [authenticatedFetch, discardOnServer]);

  const remove = useCallback(() => {
    tokenRef.current += 1;
    discardOnServer(attachmentIdFor(stateRef.current));
    setPreview(null);
    dispatch({ type: "clear" });
  }, [discardOnServer]);

  /** The photo was sent with a typed turn or read by voice; the server already deleted it. */
  const consumed = useCallback((attachmentId?: string) => {
    const current = stateRef.current;
    if (current.status !== "ready" || (attachmentId && current.attachmentId !== attachmentId)) return;
    setPreview(null);
    dispatch({ type: "consumed", attachmentId });
  }, []);

  return { state, attach, remove, consumed };
}

/** Photo button: opens a small sheet with Take photo / Choose from gallery. */
export function PhotoAttachButton({ onFile, disabled = false, compact = false }: { onFile: (file: File | undefined) => void; disabled?: boolean; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const [cameraOpen, setCameraOpen] = useState(false);
  const cameraRef = useRef<HTMLInputElement>(null);
  const galleryRef = useRef<HTMLInputElement>(null);
  const pick = (input: HTMLInputElement | null) => {
    setOpen(false);
    input?.click();
  };
  const takePhoto = () => {
    if (detectCameraPath() === "native") {
      pick(cameraRef.current);
      return;
    }
    setOpen(false);
    // Next frame: the first popup has returned focus to the 📎 button by then,
    // so closing the camera returns focus there too.
    window.requestAnimationFrame(() => setCameraOpen(true));
  };
  const chooseFiles = () => {
    setCameraOpen(false);
    galleryRef.current?.click();
  };
  const take = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) onFile(file);
  };
  const option = "flex min-h-14 w-full items-center gap-3 rounded-2xl border border-border bg-surface px-4 text-left text-sm font-semibold text-ink transition hover:bg-canvas focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus";
  return <>
    <button type="button" onClick={() => setOpen(true)} disabled={disabled} aria-label="Add a photo" aria-haspopup="dialog" className={`grid shrink-0 place-items-center rounded-full text-ink-secondary outline-none transition hover:bg-accent-soft focus-visible:ring-2 focus-visible:ring-focus/50 active:scale-[.96] disabled:cursor-not-allowed disabled:opacity-45 ${compact ? "size-11 border border-border-strong bg-surface shadow-[0_18px_40px_-22px_rgb(var(--shadow)/.6)]" : "size-10"}`}><Paperclip size={20} /></button>
    <input ref={cameraRef} type="file" accept={CAMERA_ACCEPT} capture="environment" onChange={take} className="hidden" tabIndex={-1} aria-hidden="true" />
    <input ref={galleryRef} type="file" accept={GALLERY_ACCEPT} onChange={take} className="hidden" tabIndex={-1} aria-hidden="true" />
    <InputDialog
      open={open}
      title="Add a photo"
      description="A product, a shopping list or a screenshot of one. GharSaathi reads it once, then deletes it."
      onClose={() => setOpen(false)}
      widthClassName="sm:max-w-sm"
      footer={({ requestClose }) => <button type="button" onClick={() => requestClose("cancel")} className="min-h-11 w-full rounded-full border border-accent-border px-3 text-sm font-semibold text-ink-secondary transition hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus">Cancel</button>}
    >
      <div className="space-y-2">
        <button type="button" onClick={takePhoto} className={option}><span className="grid size-9 place-items-center rounded-xl bg-accent-soft text-accent-ink"><Camera size={19} /></span>Take photo</button>
        <button type="button" onClick={() => pick(galleryRef.current)} className={option}><span className="grid size-9 place-items-center rounded-xl bg-accent-soft text-accent-ink"><ImageIcon size={19} /></span>Choose from gallery</button>
      </div>
    </InputDialog>
    <CameraDialog open={cameraOpen} onClose={() => setCameraOpen(false)} onPhoto={onFile} onChooseFiles={chooseFiles} />
  </>;
}

/** Thumbnail chip above the composer (or the voice pill) with a remove button. */
export function PhotoChip({ state, onRemove, voice = false }: { state: PhotoState; onRemove: () => void; voice?: boolean }) {
  if (state.status === "idle") return null;
  const text = state.status === "uploading" ? "Preparing photo…" : state.status === "error" ? state.message : voice ? "Photo ready · say “photo dekho”" : "Photo ready · add a note or send";
  return <div className="mx-auto mb-2 flex w-fit max-w-full items-center gap-2 rounded-2xl border border-border bg-surface py-1.5 pl-1.5 pr-2 shadow-[0_14px_30px_-22px_rgb(var(--shadow)/.7)]">
    <span className="relative grid size-10 shrink-0 place-items-center overflow-hidden rounded-xl bg-surface-muted text-muted">
      {state.previewUrl
        // eslint-disable-next-line @next/next/no-img-element -- local blob preview, never a remote image
        ? <img src={state.previewUrl} alt="Attached photo" className="size-full object-cover" />
        : state.status === "error" ? <WarningCircle size={18} className="text-danger" /> : <ImageIcon size={18} />}
      {state.status === "uploading" && <span className="absolute inset-0 grid place-items-center bg-surface/70"><CircleNotch size={16} className="animate-spin text-accent-ink" /></span>}
    </span>
    <span role="status" aria-live="polite" className={`min-w-0 truncate text-xs font-medium ${state.status === "error" ? "text-danger-hover" : "text-ink-secondary"}`}>{text}</span>
    <button type="button" onClick={onRemove} aria-label="Remove photo" className="grid size-8 shrink-0 place-items-center rounded-full text-muted transition hover:bg-surface-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"><X size={15} /></button>
  </div>;
}
