"use client";

import { CircleNotch } from "@phosphor-icons/react";
import { type ReactNode, useRef } from "react";
import { InputDialog } from "./input-dialog";

export type ConfirmDialogProps = {
  open: boolean;
  title: string;
  /** What will happen, in plain words. */
  message: ReactNode;
  confirmLabel: string;
  /** Shown on the confirm button while `busy`. */
  busyLabel?: string;
  cancelLabel?: string;
  /** "destructive" paints the confirm button red and focuses Cancel first. */
  tone?: "default" | "destructive";
  busy?: boolean;
  /** An error from the last attempt, shown above the buttons. */
  error?: string;
  onConfirm: () => void;
  onClose: () => void;
};

/** Yes/no confirmation in the shared InputDialog shell. */
export function ConfirmDialog({ open, title, message, confirmLabel, busyLabel, cancelLabel = "Cancel", tone = "default", busy = false, error, onConfirm, onClose }: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const destructive = tone === "destructive";
  return (
    <InputDialog
      open={open}
      title={title}
      role={destructive ? "alertdialog" : "dialog"}
      busy={busy}
      onClose={onClose}
      initialFocusRef={destructive ? cancelRef : confirmRef}
      widthClassName="sm:max-w-sm"
      footer={({ requestClose }) => (
        <div className="grid grid-cols-2 gap-2">
          <button ref={cancelRef} type="button" disabled={busy} onClick={() => requestClose("cancel")} className="min-h-11 rounded-full border border-accent-border px-3 text-sm font-semibold text-ink-secondary transition hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-50">{cancelLabel}</button>
          <button ref={confirmRef} type="button" disabled={busy} onClick={onConfirm} className={`flex min-h-11 items-center justify-center gap-1.5 rounded-full px-3 text-sm font-semibold text-on-accent transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:opacity-60 ${destructive ? "bg-danger hover:bg-danger-hover focus-visible:ring-danger" : "bg-accent hover:bg-accent-hover focus-visible:ring-focus"}`}>{busy && <CircleNotch size={15} className="animate-spin" />}{busy && busyLabel ? busyLabel : confirmLabel}</button>
        </div>
      )}
    >
      <div className="text-sm leading-6 text-ink-secondary">{message}</div>
      {error && <p role="alert" className="mt-3 rounded-xl bg-danger-soft px-3 py-2 text-xs leading-5 text-danger-hover">{error}</p>}
    </InputDialog>
  );
}
