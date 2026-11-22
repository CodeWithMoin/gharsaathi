/** Push-drawer and viewport maths for the chat shell (chats drawer on the left, account settings on the right). */

/** Same spring for both drawers and the pushed page. */
export const DRAWER_SPRING = Object.freeze({ type: "spring", stiffness: 420, damping: 40 });

/** Drawer panels slide with the page edge; opacity is only used for reduced motion and is fast. */
export const DRAWER_PANEL_TRANSITION = Object.freeze({ ...DRAWER_SPRING, opacity: { duration: 0.12 } });

/** Drawer width: 75% of the viewport, capped at 360px (matches `w-3/4 max-w-[360px]`). */
export function drawerWidth(viewportWidth) {
  if (!Number.isFinite(viewportWidth) || viewportWidth <= 0) return 0;
  return Math.min(viewportWidth * 0.75, 360);
}

/** Horizontal page offset in px: chats push right, settings push left, reduced motion never pushes. */
export function pushOffset(drawer, viewportWidth, reducedMotion = false) {
  if (!drawer || reducedMotion) return 0;
  const width = drawerWidth(viewportWidth);
  return drawer === "chats" ? width : -width;
}

/** Only one drawer is open at a time; opening one closes the other. */
export function drawerState(drawer) {
  return { chatsOpen: drawer === "chats", settingsOpen: drawer === "settings" };
}

/**
 * Where the window should scroll once the page is unfrozen after a drawer closes.
 * Following the latest message wins (for example a saved chat opened from the drawer);
 * otherwise the reader returns to where they were, clamped to the new page height.
 */
export function restoreScrollTarget({ savedScrollY, follow, scrollHeight, viewportHeight }) {
  const maxScroll = Math.max(0, scrollHeight - viewportHeight);
  if (follow) return maxScroll;
  return Math.min(Math.max(0, savedScrollY), maxScroll);
}

/**
 * Offsets that keep the fixed header and composer inside the visual viewport when an
 * on-screen keyboard shrinks or scrolls it (iOS Safari, Android Chrome). Pinch-zoom is
 * left alone so the bars do not chase a zoomed-in viewport.
 */
export function viewportInsets({ layoutHeight, visualHeight, visualOffsetTop, scale = 1 }) {
  if (![layoutHeight, visualHeight, visualOffsetTop, scale].every(Number.isFinite) || scale > 1.01) return { top: 0, bottom: 0 };
  const top = Math.max(0, Math.round(visualOffsetTop));
  const bottom = Math.max(0, Math.round(layoutHeight - visualHeight - visualOffsetTop));
  return { top, bottom };
}
