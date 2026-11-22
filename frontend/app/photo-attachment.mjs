// Pure logic for the composer's photo attachment (shop from a photo).

export const MAX_LONG_SIDE = 1600;
export const JPEG_QUALITY = 0.85;
// The original file may be a large camera photo; it is downscaled before upload.
export const MAX_ORIGINAL_BYTES = 40 * 1024 * 1024;
export const MAX_UPLOAD_BYTES = 8 * 1024 * 1024;
export const GALLERY_ACCEPT = "image/jpeg,image/png,image/webp,image/heic,image/heif";
export const CAMERA_ACCEPT = "image/*";
export const PHOTO_PLACEHOLDER = "📷 Photo";
// The server-made bubble thumbnail (backend app/photos/images.py: <=320 px, <=40 KB JPEG).
export const THUMBNAIL_PREFIX = "data:image/jpeg;base64,";
export const MAX_THUMBNAIL_CHARS = THUMBNAIL_PREFIX.length + Math.ceil((40 * 1024) / 3) * 4;
export const MAX_SAVED_THUMBNAILS = 20;

const ACCEPTED_TYPES = new Set(["image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"]);
const HEIF_NAME = /\.(heic|heif)$/i;

/** Scale so the longest side is at most `max`; keeps aspect ratio and never enlarges. */
export function downscaleSize(width, height, max = MAX_LONG_SIDE) {
  if (!(width > 0) || !(height > 0)) return { width: 0, height: 0 };
  const longest = Math.max(width, height);
  if (longest <= max) return { width: Math.round(width), height: Math.round(height) };
  const scale = max / longest;
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

/** HEIC/HEIF files (some browsers report an empty type for them). */
export function isHeif(file) {
  return file.type === "image/heic" || file.type === "image/heif" || (!file.type && HEIF_NAME.test(file.name ?? ""));
}

/** Null when the picked file may be prepared, else a message to show. */
export function validatePhotoFile(file) {
  if (!file) return "No photo was chosen.";
  const typeOk = ACCEPTED_TYPES.has(file.type) || isHeif(file) || (file.type?.startsWith("image/") && file.type !== "image/svg+xml" && file.type !== "image/gif");
  if (!typeOk) return "Choose a JPEG, PNG, WebP or HEIC photo.";
  if (file.size > MAX_ORIGINAL_BYTES) return "That photo is too large. Try a smaller one.";
  return null;
}

export function uploadErrorMessage(status) {
  if (status === 413) return "That photo is too large. Try a smaller one.";
  if (status === 415) return "Only JPEG, PNG, WebP or HEIC photos work.";
  if (status === 422) return "That photo couldn't be read. Try another one.";
  if (status === 403) return "Accept the terms first, then attach the photo again.";
  if (status === 401) return "Please sign in to continue.";
  return "The photo couldn't be uploaded. Try again.";
}

/** What the saved chat shows for the user's turn: a placeholder, never the image. */
export function photoMessageContent(text) {
  const clean = (text ?? "").trim();
  return clean ? `${PHOTO_PLACEHOLDER}\n${clean}` : PHOTO_PLACEHOLDER;
}

/** Only a small JPEG data URL may be shown or saved as a bubble thumbnail. */
export function isPhotoThumbnail(value) {
  return typeof value === "string" && value.startsWith(THUMBNAIL_PREFIX) && value.length <= MAX_THUMBNAIL_CHARS && /^[A-Za-z0-9+/]+=*$/.test(value.slice(THUMBNAIL_PREFIX.length));
}

/** The bubble's text when the photo itself is shown: the placeholder line is dropped. */
export function photoBubbleText(content) {
  const text = content ?? "";
  if (text === PHOTO_PLACEHOLDER) return "";
  return text.startsWith(`${PHOTO_PLACEHOLDER}\n`) ? text.slice(PHOTO_PLACEHOLDER.length + 1) : text;
}

/**
 * Saved messages keep a valid thumbnail on the newest MAX_SAVED_THUMBNAILS user
 * photo turns only; the in-session preview (a blob: URL) is never saved.
 */
export function savedPhotoFields(messages) {
  let kept = 0;
  const out = new Array(messages.length);
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const { photo_preview: _preview, photo_thumbnail: thumbnail, ...rest } = messages[index];
    void _preview;
    if (rest.role === "user" && isPhotoThumbnail(thumbnail) && kept < MAX_SAVED_THUMBNAILS) {
      kept += 1;
      out[index] = { ...rest, photo_thumbnail: thumbnail };
    } else {
      out[index] = rest;
    }
  }
  return out;
}

export const idlePhoto = Object.freeze({ status: "idle" });

/**
 * Attachment state. Every upload carries a token, so a slow upload for a photo
 * the user already replaced or removed can never overwrite the newer state.
 */
export function photoReducer(state, action) {
  switch (action.type) {
    case "start":
      return { status: "uploading", token: action.token, previewUrl: action.previewUrl ?? null };
    case "preview":
      if (state.status !== "uploading" || state.token !== action.token) return state;
      return { ...state, previewUrl: action.previewUrl ?? null };
    case "uploaded":
      if (state.status !== "uploading" || state.token !== action.token) return state;
      return { status: "ready", token: state.token, previewUrl: state.previewUrl, attachmentId: action.attachmentId, thumbnail: isPhotoThumbnail(action.thumbnail) ? action.thumbnail : null };
    case "failed":
      if (state.status !== "uploading" || state.token !== action.token) return state;
      return { status: "error", token: state.token, previewUrl: state.previewUrl, message: action.message };
    case "rejected":
      return { status: "error", token: action.token, previewUrl: null, message: action.message };
    case "consumed":
      // The assistant read this photo (voice), or a typed turn sent it.
      if (state.status === "ready" && (!action.attachmentId || state.attachmentId === action.attachmentId)) return idlePhoto;
      return state;
    case "clear":
      return idlePhoto;
    default:
      return state;
  }
}

/** The send button works with text, a ready photo, or both; never mid-upload. */
export function canSend(state, text) {
  if (state.status === "uploading") return false;
  return Boolean((text ?? "").trim()) || state.status === "ready";
}

export function attachmentIdFor(state) {
  return state.status === "ready" ? state.attachmentId : null;
}
