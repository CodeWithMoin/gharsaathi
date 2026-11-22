# GharSaathi UI decisions

These are durable product conventions for the mobile shopping chat.

- Keep the home screen chat-first: the user opens it, speaks or types, makes the important decision, and leaves.
- Treat it like a messaging app. Assistant replies keep breathing room on the right; user replies keep breathing room on the left. New turns should settle into view without abrupt scrolling.
- Never render the GharSaathi avatar beside assistant messages, including the greeting, loading, empty, basket, support, tracking, and voice transcript states. The logo exists only in the fixed header; repeated left-side icons waste mobile width and can create dark-border/cropping artifacts.
- Product comparison is decision-first: state the meaningful price/time difference before secondary details.
- For multiple products, use one compact card at a time with a stacked/deck affordance. A short swipe or a successful Add advances to the next item.
- Never let horizontal interactions create page-level overflow, clipped half-cards, or hard cut edges. Keep the active card comfortably contained and use depth/fade only as an affordance.
- Preserve explicit approval for purchases and support messages. Clearly distinguish prepared/approved from actually submitted or purchased.
- Use static demo data while iterating on UI. Live provider calls happen only when explicitly switching to live mode.

## Input popups (`frontend/app/ui/input-dialog.tsx`)

Every flow that asks the user to type or choose something opens in `InputDialog`,
never inline in whatever sheet happens to be open. Current users:

| Popup | Where | Notes |
|---|---|---|
| Add delivery address | `address-form-sheet.tsx` | Voice, typed chat, onboarding and the picker's "Add a new address" |
| Choose delivery address | `address-picker-dialog.tsx` | One store at a time; tapping an address saves it right away; Refresh; Zepto (live) can add a new address on top. Opened from the setup sheet's address rows and Settings → Addresses. No voice/typed event opens it yet (none exists) |
| Edit preference | `memory-settings.tsx` | Unsaved-changes guard; save errors show inside the popup |
| Forget everything | `memory-settings.tsx` | `ConfirmDialog`, destructive |
| Edit profile | `account-settings-sheet.tsx` | Name, how to address you and reply language in one popup with one Save; only changed fields are sent |
| Disconnect Swiggy / Zepto | `page.tsx` setup sheet | `ConfirmDialog`, destructive; closes only after the disconnect succeeds |

`ConfirmDialog` (`frontend/app/ui/confirm-dialog.tsx`) is the yes/no variant:
`title`, `message`, `confirmLabel`, `busyLabel`, `tone` (`"destructive"` paints
the button red, uses `role="alertdialog"` and focuses Cancel first), `busy`,
`error`, `onConfirm`, `onClose`. Pure logic for these flows is in
`frontend/app/input-flows.mjs`. The order card, basket − / + and the onboarding
pages are deliberately not popups.

| Prop | Type | Meaning |
|---|---|---|
| `open` | `boolean` | Shown or not |
| `title` | `string` | Heading and accessible name |
| `description` | `ReactNode?` | Supporting text under the title |
| `children` | `ReactNode` | Scrollable body |
| `footer` | `ReactNode \| ({ requestClose, presentation }) => ReactNode` | Sticky actions; use `requestClose("cancel")` for Cancel |
| `onClose` | `() => void` | Called when a close request passes the guard |
| `dirty` | `boolean?` | Unsaved changes: Escape, backdrop, X and Cancel ask before discarding |
| `busy` | `boolean?` | Saving: only `requestClose("done")` closes |
| `discardMessage` | `string?` | Text of the discard confirmation |
| `initialFocusRef` | `RefObject?` | Focus target on open (default: first focusable in the body) |
| `onShown` | `(dialog) => void?` | After the dialog is visible and laid out |
| `closeLabel` | `string?` | Label of the X button |
| `widthClassName` | `string?` | Max width when centered (default `sm:max-w-lg`) |
| `role` | `"dialog" \| "alertdialog"?` | `alertdialog` for interrupting confirmations (default `dialog`) |

Behaviour: portal to `document.body` above everything (including the voice panel),
dimmed backdrop, focus trapped and restored on close, body scroll locked, bottom
sheet under 640 px and centered above, reduced-motion respected. Pure logic
(`input-dialog-logic.mjs`) is unit-tested.

## Voice pill

While voice is on, the composer is replaced by a compact pill
(`frontend/app/voice-panel.tsx`): state icon, a live level meter, one state word
(`Connecting`, `Listening`, `Thinking`, `Speaking`, `Voice off`) and a stop button.

- The meter is driven by the real audio: a Web Audio analyser on the mic
  (`You`, ink bars) and one on GharSaathi's remote audio (orange bars),
  drawn to a canvas on `requestAnimationFrame` at up to 60 fps and paused while the
  tab is hidden (`voice-meter.ts`). Level math (RMS, decibel mapping, attack/release
  smoothing, speaker hysteresis) is pure and unit-tested (`voice-levels.mjs`).
- Reduced motion: the scrolling bars become one static level bar updated about
  8 times a second, and pill/notice transitions keep only their fades.
- Tapping the pill opens details (status text, legend, privacy line); errors and a
  blocked autoplay open them automatically with **Try again** / **Turn on sound**.
  Escape closes them. Basket, bill, order and results notices show as a short toast
  above the pill (3.6 s) and are also announced through the polite status region.

## Colour tokens and the warm-light palette

All app colour comes from semantic tokens. Values live once, as RGB channels,
in `frontend/app/globals.css` (`--color-*`), and `frontend/tailwind.config.ts`
exposes them as Tailwind colours, so opacity modifiers still work
(`bg-scrim/45`). Never hard-code a hex value in `frontend/app/**`; add or reuse
a token. `tests/unit/theme-contrast.test.mjs` fails on new hex values, checks
WCAG AA for every text/background token pair (4.5:1 body text, 3:1 large text,
icons and focus rings), and checks that Clerk's literal colours
(`app/ui/clerk-colors.mjs`, Clerk's hosted UI needs real colours) match the tokens.

Palette direction: warm but light. Warm off-white page, white surfaces, soft warm
borders, warm-charcoal text (not brown), warm-grey secondary text, and one
terracotta accent for primary actions, links, selection and focus. User chat
bubbles are a light peach tint with dark text. Dark fills (`inverse`) are
charcoal, used sparingly for secondary solid buttons, the compact toast and the
dark onboarding card. Zepto keeps its own purple only where Zepto is the actor.

| Tailwind colour | CSS variable | Value | Use |
|---|---|---|---|
| `canvas` | `--color-bg` | `#faf8f5` | Page background, quiet hover on white |
| `surface` | `--color-surface` | `#ffffff` | Cards, sheets, inputs |
| `surface-muted` | `--color-surface-muted` | `#f6f3ef` | Grouped rows, secondary panels, skeletons |
| `surface-strong` | `--color-surface-strong` | `#eee9e3` | Pressed/hover fill on muted surfaces |
| `border` | `--color-border` | `#e8e2db` | Hairlines, dividers |
| `border-strong` | `--color-border-strong` | `#d9d1c8` | Input and button outlines, disabled fills |
| `ink` | `--color-text` | `#1f1c1a` | Primary text |
| `ink-secondary` | `--color-text-secondary` | `#45403b` | Body copy, secondary labels |
| `muted` | `--color-text-muted` | `#6b645d` | Helper text, metadata |
| `subtle` | `--color-text-subtle` | `#736b64` | Smallest captions, placeholders (still AA) |
| `accent` | `--color-accent` | `#b64a18` | Primary buttons, links, selected state |
| `accent-hover` | `--color-accent-hover` | `#9d3e15` | Hover/pressed primary |
| `accent-strong` | `--color-accent-strong` | `#8f3812` | Accent text on tints, Place order |
| `accent-soft` | `--color-accent-soft` | `#fbeee6` | Selected rows, accent chips and icon wells |
| `accent-border` | `--color-accent-border` | `#f0d3c2` | Borders on accent-tinted or warm controls |
| `on-accent` | `--color-on-accent` | `#ffffff` | Text on any saturated fill (accent, danger, success, Zepto) |
| `user-bubble` / `on-user-bubble` | `--color-user-bubble` … | `#fbe9de` / `#1f1c1a` | User chat bubble |
| `inverse` / `inverse-hover` / `on-inverse` | `--color-inverse` … | `#2a2623` / `#3a3531` / `#ffffff` | Charcoal secondary buttons, toast |
| `success` / `success-strong` / `success-soft` / `success-border` | | `#3a7347` / `#2f6040` / `#edf6ef` / `#cfe5d5` | Connected, confirmed, savings |
| `warning` / `warning-soft` / `warning-border` | | `#8a5a1c` / `#fdf3e3` / `#f1dcb8` | Demo mode, price changes, cautions |
| `danger` / `danger-hover` / `danger-soft` / `danger-border` | | `#b3261e` / `#8f1f19` / `#fdecea` / `#f3c9c5` | Errors and destructive actions |
| `focus` | `--color-focus` | `#b64a18` | Focus rings |
| `zepto*` | `--color-zepto*` | unchanged purples | Zepto-branded controls only |
| `scrim` | `--color-scrim` | `#1f1c1a` | Modal backdrops (always with opacity) |
| `meter-user` / `meter-assistant` / `meter-idle` | `--color-meter-*` | `#2a2623` / `#b64a18` / `#ddd6ce` | Voice pill meter (read by `voice-meter.ts`) and its legend dots |

Shadows use `rgb(var(--shadow)/a)` (warm neutral) or `rgb(var(--accent-shadow)/a)`
inside arbitrary `shadow-[…]` values so they follow the palette.

Key contrast ratios (all checked in the test): `ink` on `canvas` 15.99,
`muted` on `canvas` 5.49, `subtle` on `surface-muted` 4.73, `accent` on white 5.27,
`accent` on `accent-soft` 4.64, white on `accent` 5.27, user bubble text 14.37,
`danger` on `danger-soft` 5.72. The two meter colours are 2.84:1 apart and differ
in hue; the pill also names the speaker in text.
