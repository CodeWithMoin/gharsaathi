"use client";

import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { X } from "@phosphor-icons/react";
import { type ReactNode, type RefObject, useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { FOCUSABLE_SELECTOR, dialogPresentation, nextFocusIndex, resolveCloseRequest, type DialogCloseSource, type DialogPresentation } from "./input-dialog-logic.mjs";

export type { DialogCloseSource, DialogPresentation };

export type InputDialogApi = {
  /** Ask to close; the unsaved-changes guard applies unless source is "done". */
  requestClose: (source?: DialogCloseSource) => void;
  presentation: DialogPresentation;
};

export type InputDialogProps = {
  /** Whether the dialog is shown. */
  open: boolean;
  /** Heading, also the dialog's accessible name. */
  title: string;
  /** Optional supporting text under the title. */
  description?: ReactNode;
  /** Scrollable body, usually a form. */
  children: ReactNode;
  /** Sticky footer actions. A function receives `requestClose` for Cancel buttons. */
  footer?: ReactNode | ((api: InputDialogApi) => ReactNode);
  /** Called when a close request passes the guard. */
  onClose: () => void;
  /** The body has unsaved changes: Escape, backdrop and Cancel ask before discarding. */
  dirty?: boolean;
  /** A save is running: nothing but "done" closes the dialog. */
  busy?: boolean;
  /** Text of the discard confirmation. */
  discardMessage?: string;
  /** Element to focus on open; defaults to the first focusable element in the body. */
  initialFocusRef?: RefObject<HTMLElement | null>;
  /** Called once the dialog is visible and laid out (e.g. to report it to the assistant). */
  onShown?: (dialog: HTMLElement) => void;
  /** Accessible label of the close (X) button. */
  closeLabel?: string;
  /** Max width of the centered presentation, as a Tailwind class. */
  widthClassName?: string;
  /** "alertdialog" for confirmations that interrupt (e.g. destructive actions). */
  role?: "dialog" | "alertdialog";
};

function useViewportWidth(): number {
  const [width, setWidth] = useState(() => (typeof window === "undefined" ? 1024 : window.innerWidth));
  useEffect(() => {
    const update = () => setWidth(window.innerWidth);
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);
  return width;
}

/** Modal dialog for any flow that needs user input: bottom sheet under 640px, centered above. */
export function InputDialog({ open, title, description, children, footer, onClose, dirty = false, busy = false, discardMessage = "Discard your changes?", initialFocusRef, onShown, closeLabel = "Close", widthClassName = "sm:max-w-lg", role = "dialog" }: InputDialogProps) {
  const reduceMotion = useReducedMotion();
  const presentation = dialogPresentation(useViewportWidth());
  const titleId = useId();
  const descriptionId = useId();
  const panelRef = useRef<HTMLDivElement | null>(null);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const restoreRef = useRef<HTMLElement | null>(null);
  const stateRef = useRef({ dirty, busy, onClose, discardMessage, onShown });
  useEffect(() => { stateRef.current = { dirty, busy, onClose, discardMessage, onShown }; });
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const requestClose = useCallback((source: DialogCloseSource = "cancel") => {
    const state = stateRef.current;
    const decision = resolveCloseRequest({ dirty: state.dirty, busy: state.busy, source, confirmDiscard: () => window.confirm(state.discardMessage) });
    if (decision === "close") state.onClose();
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    restoreRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
      const restore = restoreRef.current;
      if (restore && document.contains(restore)) restore.focus({ preventScroll: true });
    };
  }, [open]);

  useEffect(() => {
    if (!open || !mounted) return;
    const frame = window.requestAnimationFrame(() => {
      const panel = panelRef.current;
      if (!panel) return;
      if (bodyRef.current) bodyRef.current.scrollTop = 0;
      const target = initialFocusRef?.current ?? bodyRef.current?.querySelector<HTMLElement>(FOCUSABLE_SELECTOR) ?? panel;
      target.focus({ preventScroll: true });
      target.scrollIntoView?.({ block: "nearest" });
      stateRef.current.onShown?.(panel);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [open, mounted, initialFocusRef]);

  const onKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      requestClose("escape");
      return;
    }
    if (event.key !== "Tab" || !panelRef.current) return;
    const focusables = [...panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)].filter((element) => element.offsetParent !== null || element === document.activeElement);
    const index = nextFocusIndex(focusables.length, focusables.indexOf(document.activeElement as HTMLElement), event.shiftKey);
    event.preventDefault();
    (index < 0 ? panelRef.current : focusables[index]).focus();
  }, [requestClose]);

  if (!mounted) return null;
  const sheet = presentation === "sheet";
  const api: InputDialogApi = { requestClose, presentation };
  const motionProps = reduceMotion
    ? { initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 }, transition: { duration: 0.01 } }
    : sheet
      ? { initial: { y: "100%" }, animate: { y: 0 }, exit: { y: "100%" }, transition: { type: "spring" as const, damping: 32, stiffness: 340 } }
      : { initial: { opacity: 0, scale: 0.97, y: 8 }, animate: { opacity: 1, scale: 1, y: 0 }, exit: { opacity: 0, scale: 0.98, y: 4 }, transition: { duration: 0.18 } };

  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div key="input-dialog" className={`fixed inset-0 z-[90] flex ${sheet ? "items-end" : "items-center justify-center p-4"}`} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: reduceMotion ? 0.01 : 0.15 }}>
          <div className="absolute inset-0 bg-[#2a1b14]/45 backdrop-blur-[2px]" aria-hidden="true" onMouseDown={() => requestClose("backdrop")} />
          <motion.div
            ref={panelRef}
            role={role}
            aria-modal="true"
            aria-labelledby={titleId}
            aria-describedby={description ? descriptionId : undefined}
            tabIndex={-1}
            onKeyDown={onKeyDown}
            {...motionProps}
            className={`relative flex w-full flex-col overflow-hidden bg-[#fffdf9] text-[#3b2a22] shadow-[0_30px_80px_-30px_rgba(42,27,20,.7)] outline-none ${sheet ? "max-h-[92dvh] rounded-t-[26px]" : `max-h-[88dvh] rounded-[24px] ${widthClassName}`}`}
          >
            {sheet && <span className="mx-auto mt-2.5 h-1 w-10 shrink-0 rounded-full bg-[#e2d3c7]" aria-hidden="true" />}
            <header className="flex shrink-0 items-start gap-3 border-b border-[#efe4db] px-5 pb-4 pt-4">
              <div className="min-w-0 flex-1">
                <h2 id={titleId} className="font-display text-lg font-semibold leading-6">{title}</h2>
                {description && <div id={descriptionId} className="mt-1 text-xs leading-5 text-[#806f65]">{description}</div>}
              </div>
              <button type="button" onClick={() => requestClose("close_button")} disabled={busy} className="grid size-9 shrink-0 place-items-center rounded-full text-[#8c776b] transition hover:bg-[#f3e7dd] hover:text-[#3b2a22] disabled:opacity-40" aria-label={closeLabel}><X size={18} /></button>
            </header>
            <div ref={bodyRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 py-4">{children}</div>
            {footer && <footer className="shrink-0 border-t border-[#efe4db] bg-[#fffdf9] px-5 py-3 pb-[max(.75rem,env(safe-area-inset-bottom))]">{typeof footer === "function" ? footer(api) : footer}</footer>}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
