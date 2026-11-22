export declare const BOTTOM_SHEET_MAX_WIDTH: number;
export declare const FOCUSABLE_SELECTOR: string;
export type DialogPresentation = "sheet" | "centered";
export type DialogCloseSource = "escape" | "backdrop" | "cancel" | "close_button" | "done";
export function dialogPresentation(viewportWidth: number): DialogPresentation;
export function resolveCloseRequest(input: { dirty: boolean; busy?: boolean; source: DialogCloseSource; confirmDiscard?: () => boolean }): "close" | "stay";
export function nextFocusIndex(count: number, current: number, backwards: boolean): number;
export function hasUnsavedChanges(initial: Record<string, unknown>, current: Record<string, unknown>): boolean;
