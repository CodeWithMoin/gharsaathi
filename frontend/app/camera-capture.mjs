// Pure logic for "Take photo": native capture input on phones, webcam dialog elsewhere.

export const CAMERA_IDEAL_WIDTH = 1600;
// The camera frame is encoded once here, then the shared photo pipeline
// downscales and re-encodes it at JPEG_QUALITY before upload.
export const CAPTURE_JPEG_QUALITY = 0.92;
export const CAPTURE_FILE_NAME = "camera.jpg";

const MOBILE_UA = /Android|iPhone|iPad|iPod|Mobile|Silk|Kindle|BlackBerry|Opera Mini|IEMobile/i;
const BACK_CAMERA_LABEL = /\b(back|rear|environment|world)\b/i;
const FRONT_CAMERA_LABEL = /\b(front|user|facetime|selfie)\b/i;

/**
 * "native" when the `capture` attribute opens the device camera (phones, tablets,
 * or browsers without getUserMedia, where the file input is the only option);
 * "webcam" on desktops and laptops, where `capture` just opens the file picker.
 */
export function cameraPath({ userAgent = "", platform = "", maxTouchPoints = 0, coarsePointer = false, mobileHint, hasGetUserMedia = false } = {}) {
  if (!hasGetUserMedia) return "native";
  if (mobileHint === true) return "native";
  if (MOBILE_UA.test(userAgent)) return "native";
  // iPadOS reports a desktop Mac user agent; only real Macs have no touch points.
  const macLike = /Macintosh|Mac OS X/i.test(userAgent) || /^Mac/i.test(platform);
  if (macLike && maxTouchPoints > 1) return "native";
  // Touch-first devices without a mobile user agent (some tablets, Android in desktop mode).
  if (coarsePointer && maxTouchPoints > 0) return "native";
  return "webcam";
}

/** getUserMedia constraints: one camera, never audio. */
export function cameraConstraints({ deviceId, facingMode = "user" } = {}) {
  const video = deviceId
    ? { deviceId: { exact: deviceId }, width: { ideal: CAMERA_IDEAL_WIDTH } }
    : { facingMode, width: { ideal: CAMERA_IDEAL_WIDTH } };
  return { audio: false, video };
}

/** The video inputs from `enumerateDevices`, with a readable label for each. */
export function videoInputs(devices) {
  return (devices ?? [])
    .filter((device) => device && device.kind === "videoinput" && device.deviceId)
    .map((device, index) => ({ deviceId: device.deviceId, label: (device.label ?? "").trim() || `Camera ${index + 1}` }));
}

/** The camera after `currentId`, wrapping round; null when there is nothing to switch to. */
export function nextCamera(cameras, currentId) {
  if (!cameras || cameras.length < 2) return null;
  const index = cameras.findIndex((camera) => camera.deviceId === currentId);
  return cameras[(index + 1) % cameras.length];
}

/**
 * Whether the live preview is mirrored. Front cameras are, like a mirror; back
 * cameras are not. Laptop webcams often report no facing mode and face the user.
 * Only the preview is mirrored, never the captured photo.
 */
export function mirrorPreview({ facingMode, label } = {}) {
  if (facingMode === "user") return true;
  if (facingMode === "environment" || facingMode === "left" || facingMode === "right") return false;
  if (label && BACK_CAMERA_LABEL.test(label)) return false;
  if (label && FRONT_CAMERA_LABEL.test(label)) return true;
  return true;
}

/** Canvas size for a captured frame: the stream's own resolution, falling back to track settings. */
export function captureSize(videoWidth, videoHeight, settings = {}) {
  const valid = (width, height) => Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0;
  if (valid(videoWidth, videoHeight)) return { width: Math.round(videoWidth), height: Math.round(videoHeight) };
  if (valid(settings.width, settings.height)) return { width: Math.round(settings.width), height: Math.round(settings.height) };
  return { width: 0, height: 0 };
}

/** Map a getUserMedia error to a state the dialog can explain. */
export function cameraErrorKind(error) {
  const name = error && typeof error === "object" ? error.name : "";
  if (name === "NotAllowedError" || name === "PermissionDeniedError" || name === "SecurityError") return "denied";
  if (name === "NotFoundError" || name === "DevicesNotFoundError" || name === "OverconstrainedError" || name === "ConstraintNotSatisfiedError") return "not_found";
  if (name === "NotReadableError" || name === "TrackStartError" || name === "AbortError") return "in_use";
  return "unknown";
}

export function cameraErrorMessage(kind) {
  if (kind === "denied") return "Allow camera access in your browser settings, or choose a photo from your files.";
  if (kind === "not_found") return "No camera was found. Connect one, or choose a photo from your files.";
  if (kind === "in_use") return "Your camera is being used by another app. Close it and try again, or choose a photo from your files.";
  return "The camera couldn't start. Try again, or choose a photo from your files.";
}

export const idleCamera = Object.freeze({ status: "idle" });

/** Dialog state: idle → starting → live → captured, or error; paused while the tab is hidden. */
export function cameraReducer(state, action) {
  switch (action.type) {
    case "start":
      return { status: "starting", prompt: Boolean(action.prompt) };
    case "live":
      return state.status === "starting" ? { status: "live" } : state;
    case "captured":
      return state.status === "live" ? { status: "captured" } : state;
    case "failed":
      return state.status === "starting" || state.status === "live" ? { status: "error", kind: action.kind } : state;
    case "paused":
      return state.status === "starting" || state.status === "live" ? { status: "paused" } : state;
    case "reset":
      return idleCamera;
    default:
      return state;
  }
}

/** Text for the dialog's live status region. */
export function cameraStatusText(state) {
  switch (state.status) {
    case "starting":
      return state.prompt ? "Allow camera access when your browser asks." : "Starting the camera…";
    case "live":
      return "Camera is on. Press Capture to take the photo.";
    case "captured":
      return "Photo taken. Use it, or retake.";
    case "paused":
      return "Camera paused while this tab was hidden.";
    case "error":
      return cameraErrorMessage(state.kind);
    default:
      return "";
  }
}
