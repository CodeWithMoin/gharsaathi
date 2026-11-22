export const CAMERA_IDEAL_WIDTH: number;
export const CAPTURE_JPEG_QUALITY: number;
export const CAPTURE_FILE_NAME: string;

export type CameraPath = "native" | "webcam";
export type CameraErrorKind = "denied" | "not_found" | "in_use" | "unknown";
export type CameraOption = { deviceId: string; label: string };

export type CameraState =
  | { status: "idle" }
  | { status: "starting"; prompt: boolean }
  | { status: "live" }
  | { status: "captured" }
  | { status: "paused" }
  | { status: "error"; kind: CameraErrorKind };

export type CameraAction =
  | { type: "start"; prompt?: boolean }
  | { type: "live" }
  | { type: "captured" }
  | { type: "failed"; kind: CameraErrorKind }
  | { type: "paused" }
  | { type: "reset" };

export function cameraPath(environment?: {
  userAgent?: string;
  platform?: string;
  maxTouchPoints?: number;
  coarsePointer?: boolean;
  mobileHint?: boolean;
  hasGetUserMedia?: boolean;
}): CameraPath;
export function cameraConstraints(options?: { deviceId?: string; facingMode?: "user" | "environment" }): MediaStreamConstraints;
export function videoInputs(devices: ReadonlyArray<{ kind: string; deviceId: string; label?: string }> | null | undefined): CameraOption[];
export function nextCamera(cameras: ReadonlyArray<CameraOption> | null | undefined, currentId: string | null | undefined): CameraOption | null;
export function mirrorPreview(camera?: { facingMode?: string; label?: string }): boolean;
export function captureSize(videoWidth: number, videoHeight: number, settings?: { width?: number; height?: number }): { width: number; height: number };
export function cameraErrorKind(error: unknown): CameraErrorKind;
export function cameraErrorMessage(kind: CameraErrorKind): string;
export const idleCamera: CameraState;
export function cameraReducer(state: CameraState, action: CameraAction): CameraState;
export function cameraStatusText(state: CameraState): string;
