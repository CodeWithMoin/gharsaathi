export const MAX_LONG_SIDE: number;
export const JPEG_QUALITY: number;
export const MAX_ORIGINAL_BYTES: number;
export const MAX_UPLOAD_BYTES: number;
export const GALLERY_ACCEPT: string;
export const CAMERA_ACCEPT: string;
export const PHOTO_PLACEHOLDER: string;
export const THUMBNAIL_PREFIX: string;
export const MAX_THUMBNAIL_CHARS: number;
export const MAX_SAVED_THUMBNAILS: number;

export type PhotoState =
  | { status: "idle" }
  | { status: "uploading"; token: number; previewUrl: string | null }
  | { status: "ready"; token: number; previewUrl: string | null; attachmentId: string; thumbnail: string | null }
  | { status: "error"; token: number; previewUrl: string | null; message: string };

export type PhotoAction =
  | { type: "start"; token: number; previewUrl?: string | null }
  | { type: "preview"; token: number; previewUrl: string | null }
  | { type: "uploaded"; token: number; attachmentId: string; thumbnail?: string | null }
  | { type: "failed"; token: number; message: string }
  | { type: "rejected"; token: number; message: string }
  | { type: "consumed"; attachmentId?: string }
  | { type: "clear" };

export function downscaleSize(width: number, height: number, max?: number): { width: number; height: number };
export function isHeif(file: { type: string; name?: string }): boolean;
export function validatePhotoFile(file: { type: string; size: number; name?: string } | null | undefined): string | null;
export function uploadErrorMessage(status: number): string;
export function photoMessageContent(text: string | null | undefined): string;
export const idlePhoto: PhotoState;
export function photoReducer(state: PhotoState, action: PhotoAction): PhotoState;
export function canSend(state: PhotoState, text: string | null | undefined): boolean;
export function attachmentIdFor(state: PhotoState): string | null;
export function isPhotoThumbnail(value: unknown): value is string;
export function photoBubbleText(content: string | null | undefined): string;
export function savedPhotoFields<M extends { role: string; photo_thumbnail?: string | null; photo_preview?: string | null }>(messages: M[]): Omit<M, "photo_preview">[];
export const PHOTO_BETA_LABEL: string;
export const PHOTO_BETA_NOTE: string;
