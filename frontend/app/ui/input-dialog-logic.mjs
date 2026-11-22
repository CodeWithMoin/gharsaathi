// Pure helpers behind InputDialog: kept free of React so they can be unit-tested.

export const BOTTOM_SHEET_MAX_WIDTH = 640;

export const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type=hidden])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

/** @param {number} viewportWidth */
export function dialogPresentation(viewportWidth) {
  return Number.isFinite(viewportWidth) && viewportWidth < BOTTOM_SHEET_MAX_WIDTH ? "sheet" : "centered";
}

/**
 * What a close request should do.
 * @param {{ dirty: boolean, busy?: boolean, source: "escape" | "backdrop" | "cancel" | "close_button" | "done", confirmDiscard?: () => boolean }} input
 * @returns {"close" | "stay"}
 */
export function resolveCloseRequest({ dirty, busy = false, source, confirmDiscard }) {
  if (source === "done") return "close";
  if (busy) return "stay";
  if (!dirty) return "close";
  if (typeof confirmDiscard !== "function") return "stay";
  return confirmDiscard() ? "close" : "stay";
}

/**
 * Next focus index for Tab inside the dialog (focus trap). Returns -1 when
 * there is nothing focusable.
 * @param {number} count
 * @param {number} current index of document.activeElement among the focusables, or -1
 * @param {boolean} backwards Shift+Tab
 */
export function nextFocusIndex(count, current, backwards) {
  if (count <= 0) return -1;
  if (current < 0) return backwards ? count - 1 : 0;
  if (backwards) return current === 0 ? count - 1 : current - 1;
  return current === count - 1 ? 0 : current + 1;
}

/**
 * Whether two shallow value maps differ (the "unsaved changes" signal for forms).
 * @param {Record<string, unknown>} initial
 * @param {Record<string, unknown>} current
 */
export function hasUnsavedChanges(initial, current) {
  const keys = new Set([...Object.keys(initial ?? {}), ...Object.keys(current ?? {})]);
  for (const key of keys) {
    const before = initial?.[key] ?? "";
    const after = current?.[key] ?? "";
    if (String(before).trim() !== String(after).trim()) return true;
  }
  return false;
}
