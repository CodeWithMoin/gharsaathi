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

## Header, address first and the settings drawer

Mobile-first (phone browsers and the Home Screen app); desktop shows the same centred column.

**Header** (`home-header-bar.tsx`, logic `headerAddress` in `home-header.mjs`), Swiggy-style,
one 68 px row: **☰** (recent chats, left drawer, 44 px, far left) · the **address block**
(pin, bold label + ▾, one muted line of the address, both truncate; tap → Choose delivery
address for that store) · **basket** with its item badge (only when the basket has items) ·
**avatar** (photo or initials, 44 px; opens the right drawer). The avatar replaced the old
store icon, which only opened Settings; basket access was already its own cart button, so it
stays a cart icon with the badge beside the avatar. ☰ stays in the header rather than moving
recent chats into the avatar drawer: at 360 px the address block still gets about 190 px. A
moving order's live chip sits on a second header row (a 44 px chip; the page adds 52 px of top padding),
so the address never shares the row with it. The block prefers Zepto's selected address
(Zepto places orders), then Swiggy's; with no valid address it reads "Choose address · For
Zepto"; with no store "Connect a store" (tap → Settings → Stores).

**Address first** (`addressGate`, `deliver-first.tsx`): on open or a new chat, when a store
is connected, every connected store has finished loading its saved addresses, and none has a
valid selection (never chosen, or the chosen one is gone), the home screen shows **Where should
we deliver?** instead of the greeting and composer: saved addresses as large rows (one tap
saves it and the chat continues), **Add address** (Zepto, live) and **Refresh from …**. It
is inline like the onboarding pages, not a popup: it is the first step of the home screen,
not an interruption. With an address chosen nothing blocks and the header is the only place
the address shows (the greeting does not repeat it). Changing the address keeps the existing
behaviour: bill and order cards are invalidated by the basket/address rules and the shared
delivery line is revoked (`delivery-line-panel.tsx`).

**Right drawer** (`account-settings-sheet.tsx`, map in `settings-map.mjs`): one width on the
overview and every subpage, `w-[85vw] max-w-[400px]` (`SETTINGS_DRAWER_CLASS`; the page is
pushed by the same `drawerWidth(…, "settings")`), wide enough for memory rows with badges and
Edit/Delete. Every subpage uses the same header (back · title · close) and spacing.

| Row | Opens |
|---|---|
| Profile header row (avatar, name, email) | **Profile**: name, how to address you, reply language (Edit profile popup), reply style (Quick/Detailed), **Notifications** switch |
| Stores | Shopping mode, Connect (the store's own login, callback checked first) / **Disconnect** (confirm popup), **Change … address** per connected store |
| What GharSaathi knows | Memory switch, saved items, History, Privacy (above) |
| Delivery line (Beta) | What it does, the configured AI number (display only), status from `/api/inbound-delivery/config`, how to use (decline the call, or press the side button twice), forwarding **when busy only**, off with `##002#` (never unanswered/unreachable: the AI's transfer back would loop), **Directions for this address** (the directions editor popup below) |
| Rider assist (Beta) | The parked Vobiz page; the row exists only when `/api/rider/settings` reports `feature_enabled` and `owner_configured` (`riderAssistAvailable`) |
| **Sign out** | A small text link under the rows |

Removed from the drawer: Addresses (→ header), Notifications (→ Profile), Privacy & memory and
"More preferences" (→ What GharSaathi knows), Help with an order (→ order card). Nothing in
Settings opens the legacy setup sheet or reopens onboarding (unit-tested); the setup sheet is
kept only for the OAuth return, "not connected" voice/chat events and the direct-search fallback.

**Order card links** (`orderCardLinks`, `order-status-card.tsx`): under a moving order,
**Delivery line ready** / **Set up delivery line** (→ Settings → Delivery line), **Directions**
(→ the directions editor) and **Need help?** (→ Order help, always shown, also on a delivered
order once expanded). One row of equal cells (icon above a short label, 56 px tall; a lone
Need help? is a single left-aligned row). The header chip and the card's "Arriving by…" both
read the order's `etaAt`.

**Directions for this address** (`InputDialog` in `page.tsx` wrapping `DeliveryLinePanel`):
landmark, route steps, gate note and handover for the selected address of that store, and
sharing it with the delivery line. Opened from Settings → Delivery line and the order card.

The drawer moves focus to the panel itself on open, not to the close button, so no focus ring
appears after a tap; keyboard users Tab to Back/Close and get the normal focus-visible ring.

## Input popups (`frontend/app/ui/input-dialog.tsx`)

Every flow that asks the user to type or choose something opens in `InputDialog`,
never inline in whatever sheet happens to be open. Current users:

| Popup | Where | Notes |
|---|---|---|
| Add delivery address | `address-form-sheet.tsx` | Voice, typed chat, onboarding and the picker's "Add a new address" |
| Choose delivery address | `address-picker-dialog.tsx` | One store at a time; tapping an address saves it right away; Refresh; Zepto (live) can add a new address on top. Opened from the header address block, "Change … address" on Settings → Stores and the setup sheet's address rows. No voice/typed event opens it yet (none exists) |
| Edit preference | `memory-settings.tsx` | Unsaved-changes guard; save errors show inside the popup; fixed-set answers (diet, value preference, home questions) are a choice |
| Delete everything | `memory-settings.tsx` | `ConfirmDialog`, destructive; name, language and order history stay |
| Remove order history | `memory-settings.tsx` (History) | `ConfirmDialog`, destructive; store orders are not changed |

Settings → **What GharSaathi knows** (`memory-settings.tsx`, view logic `summaryView` in
`memory-groups.mjs`) starts with the **Memory** switch ("Use what I've told you"), then lists
everything saved in six groups: Home, Food, Usual buys, Avoid, Shopping style, From your
orders. Each row shows its source (You told us · Said in passing · From your repeated choices ·
From your orders · From demo history) and when it was updated, with Edit and Delete (profile
rows: Edit opens Profile, Reset goes back to the default; order rows are read-only). Below the
groups: **History** (Learn from my orders on/off, how long imported order data is kept,
Refresh usuals, Re-import full history, weekly sync per store, Remove order history) and
**Privacy** (Delete everything, Privacy Policy link). "Memory" is the one word for what
GharSaathi remembers; "saved preferences" is not used anywhere (unit-tested). "What do you
know about me?" by voice or chat opens it (`memory.summary` event).
| Edit profile | `account-settings-sheet.tsx` (Settings → Profile) | Name, how to address you and reply language in one popup with one Save; only changed fields are sent |
| Disconnect Swiggy / Zepto | Settings → Stores **Disconnect** and the `page.tsx` setup sheet | `ConfirmDialog`, destructive; closes only after the disconnect succeeds |
| Add a photo | `photo-attachment-ui.tsx` (📎 in the composer and beside the voice pill) | Two choices: **Take photo** and and **Choose from gallery**; the file inputs live outside the popup. On phones and tablets **Take photo** clicks the native `capture="environment"` input; elsewhere it opens **Take a photo**. The picked photo shows at once (local preview, before upload finishes) as a thumbnail chip with **Remove photo** above the composer or pill |
| Take a photo (webcam) | `camera-dialog.tsx`, logic in `camera-capture.mjs` | Desktops/laptops only (`cameraPath()`: no mobile UA, not iPadOS, not touch-first, `getUserMedia` present). Live preview (video only, about 1600 px wide, front camera mirrored in the preview only), **Capture** (focused on open) → **Retake** / **Use photo**, **Cancel**, a switch-camera button when there is more than one camera. Permission, denied ("Allow camera access in your browser settings, or choose a photo from your files" + **Choose from files**), no camera and camera-in-use states are read out by a status line. Tracks stop on close, capture, unmount and when the tab is hidden. The frame goes through the same downscale/JPEG/upload pipeline as a picked photo |
| Your photo (larger view) | `PhotoBubbleImage` in `photo-attachment-ui.tsx` | Tapping the photo in a user bubble. This session shows the local copy; reopened chats show the saved ≤320 px thumbnail. Chats saved before thumbnails show the "📷 Photo" text only |
| Track order | `order-status-card.tsx` | Opens `LiveOrderTracking` on the placed order's monitor from the chat status card; read-only |
| My lists | `saved-lists-dialog.tsx` (quick chip "My lists", basket card "Save as list") | The user's own lists: **Add to basket** per list, **New list** (by name), **Save basket as list**. **Edit list** (same popup): name, − / + / remove per item, **Add basket items**, **Delete list** (`ConfirmDialog`, destructive). Unsaved-changes guard. Logic in `quick-actions.mjs` (`listDraft`, `draftStep`, `draftPayload`, `listNameProblem`) |
| Clear basket? | `basket/BasketPanel.tsx` | `ConfirmDialog`, destructive: "This removes all 3 items from your Zepto basket. Any bill or order card for it stops working." One request, `POST /api/baskets/{provider}/clear` with the revision the card shows; 409 closes the popup and says "This basket changed. Review it and try again." |

`ConfirmDialog` (`frontend/app/ui/confirm-dialog.tsx`) is the yes/no variant:
`title`, `message`, `confirmLabel`, `busyLabel`, `tone` (`"destructive"` paints
the button red, uses `role="alertdialog"` and focuses Cancel first), `busy`,
`error`, `onConfirm`, `onClose`. Pure logic for these flows is in
`frontend/app/input-flows.mjs`. The order card, basket − / + and the onboarding
pages are deliberately not popups. The order card's payment-method chips
(`role="radio"`, shown only when more than one method is offered) stay inline
too; choosing one replaces the card. Prepaid cards say **Pay ₹…**, then show
**Open payment** (Zepto's own link) and **I've paid — check status**; no field
ever asks for a PIN, card number or OTP.

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

## Browser notifications

- Opt-in only. Settings → **Profile** has one **Notifications** switch with its status and
  hint (`NotificationSwitch`); the browser permission prompt appears only after that tap,
  never on load. Every kind of update stays on (the server default); there is no per-kind
  checklist in Settings any more.
- After the first placed order, a one-time inline prompt above the composer: "Get
  delivery updates as notifications?" with **Not now** and **Turn on**. Either answer
  is remembered on the server, so it never shows again (on any device).
- Blocked, unsupported, insecure (plain-http LAN address), iPhone/iPad not added to
  the Home Screen, and "not set up on this server" each show one plain sentence and a
  disabled switch. Logic: `push-notifications.mjs`; UI: `notification-settings.tsx`.
- While the app is visible and focused, updates appear only in-app (toast/status
  card); the service worker shows system notifications only when it is not.
- Tapping a notification opens or focuses the app and scrolls to that order's status card.

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
| `canvas` | `--color-bg` | `#f4efe8` | Page background, quiet hover on white |
| `surface` | `--color-surface` | `#fffdfa` | Cards, sheets, inputs |
| `surface-muted` | `--color-surface-muted` | `#efe8df` | Grouped rows, secondary panels, skeletons |
| `surface-strong` | `--color-surface-strong` | `#e6ddd2` | Pressed/hover fill on muted surfaces |
| `border` | `--color-border` | `#e0d6ca` | Hairlines, dividers |
| `border-strong` | `--color-border-strong` | `#cbbfb1` | Input and button outlines, disabled fills |
| `ink` | `--color-text` | `#1a1613` | Primary text |
| `ink-secondary` | `--color-text-secondary` | `#332d28` | Body copy, secondary labels |
| `muted` | `--color-text-muted` | `#4a423b` | Helper text, metadata |
| `subtle` | `--color-text-subtle` | `#524a43` | Smallest captions, placeholders (still AA) |
| `accent` | `--color-accent` | `#c2561f` | Filled primary buttons, mic, selected fills (white text) |
| `accent-ink` | `--color-accent-ink` | `#a3401a` | Accent-coloured text and links on light backgrounds |
| `accent-hover` | `--color-accent-hover` | `#8a3614` | Hover/pressed primary |
| `accent-strong` | `--color-accent-strong` | `#7a2f11` | Accent text on tints, Place order |
| `accent-soft` | `--color-accent-soft` | `#f8e6da` | Selected rows, accent chips and icon wells |
| `accent-border` | `--color-accent-border` | `#e8c4ae` | Borders on accent-tinted or warm controls |
| `on-accent` | `--color-on-accent` | `#ffffff` | Text on any saturated fill (accent, danger, success, Zepto) |
| `user-bubble` / `on-user-bubble` | `--color-user-bubble` … | `#f3d9c6` / `#1a1613` | User chat bubble |
| `inverse` / `inverse-hover` / `on-inverse` | `--color-inverse` … | `#231f1c` / `#35302b` / `#ffffff` | Charcoal secondary buttons, toast |
| `success` / `success-strong` / `success-soft` / `success-border` | | `#3a7347` / `#2f6040` / `#edf6ef` / `#cfe5d5` | Connected, confirmed, savings |
| `warning` / `warning-soft` / `warning-border` | | `#8a5a1c` / `#fdf3e3` / `#f1dcb8` | Demo mode, price changes, cautions |
| `danger` / `danger-hover` / `danger-soft` / `danger-border` | | `#b3261e` / `#8f1f19` / `#fdecea` / `#f3c9c5` | Errors and destructive actions |
| `focus` | `--color-focus` | `#a3401a` | Focus rings |
| `zepto*` | `--color-zepto*` | unchanged purples | Zepto-branded controls only |
| `scrim` | `--color-scrim` | `#1a1613` | Modal backdrops (always with opacity) |
| `meter-user` / `meter-assistant` / `meter-idle` | `--color-meter-*` | `#231f1c` / `#a3401a` / `#d3c8bb` | Voice pill meter (read by `voice-meter.ts`) and its legend dots |

Shadows use `rgb(var(--shadow)/a)` (warm neutral) or `rgb(var(--accent-shadow)/a)`
inside arbitrary `shadow-[…]` values so they follow the palette.

Key contrast ratios (all checked in the test): `ink` on `canvas` 15.99,
`muted` on `canvas` 5.49, `subtle` on `surface-muted` 4.73, `accent` on white 5.27,
`accent` on `accent-soft` 4.64, white on `accent` 5.27, user bubble text 14.37,
`danger` on `danger-soft` 5.72. The two meter colours are 2.84:1 apart and differ
in hue; the pill also names the speaker in text.

## Quick chips, card edits and the live order chip

- **Quick chips** (`quick-chips.tsx`, rules in `quick-actions.mjs` `quickChips`): one
  horizontally scrolling row, at most 6, under the greeting on the empty home screen and
  above the composer once the chat has turns (not in voice mode). Order: **Reorder last
  order** (only when there is one), **Mera order kahan hai?** (only while an order in this
  chat is moving), up to 3 recent products (the last distinct products this household
  really ordered; synthetic sample orders never count), **My lists** (always). Nothing is predicted.
- A chip tap is the user's own turn: it appears in the timeline as a user bubble
  ("Reorder my last order", "Add Amul Milk 500 ml", "Add my “Weekly list” to the basket"),
  then one plain-English assistant line ("Added 3 items from your last Zepto order to
  your basket. Not added: Amul Curd (unavailable at Zepto right now). Tap “Get final
  bill” on the basket below when you're ready."), then the basket card at that point.
  "Mera order kahan hai?" sends a normal chat turn. A recent item also shows a toast with
  **Undo** (6 s), which sets the line back to its earlier quantity.
- Every item is re-checked at its store before it is added (same variant, available,
  today's price); errors are named as errors, never "not found". Nothing is quoted or
  ordered by a chip.
- **Clear basket:** chosen as a confirm popup, not an Undo toast. Undo would have to
  re-add every line (several requests, prices may have moved, and the bill/order card it
  superseded cannot come back); a confirmation is one honest step. Disabled with the
  reason shown while checkout is pending or its outcome is unknown, and on an ordered
  basket.
- **Basket card lines:** − / + / remove (as before) and **Swap**, which searches that item
  and shows the usual option carousel; adding an option from it replaces the line (one
  remove after the add). Every edit uses the basket line API and supersedes any open bill
  or order card.
- **Live order chip** in the header while an order in this chat is moving: "Zepto ·
  arriving 8:26", "Out for delivery · 8:26", "Running late", "Zepto · Being packed" (the
  accessible label always names the store). Only the store's own status and ETA (`headerOrderChip`). Tap scrolls to the
  order's status card. It truncates on a 390 px header.
- Reduced motion: chips and the toast keep only colour/opacity changes (no press scale or
  slide); spinners stop. Every chip and action has a visible focus ring.

## Delivery line panel (`frontend/app/delivery-line-panel.tsx`)

- Lives in **Track an order**. The address is the store address selected in the app,
  shown **read-only**; **Change address** opens the same address picker. The panel never
  offers a free-text address, so what the line says always matches the selected address.
- **Recipient** comes from the profile name; **Edit** saves it to the profile once.
- **Directions for this address** (landmark, up to 8 short steps, optional gate note,
  optional handover with quick-pick chips) are edited in an `InputDialog` and reused for
  that address next time; the bin in the popup deletes them (confirm popup).
- **Share with delivery line** appears only when an address, a name and a landmark exist,
  and stays disabled until the approval box is ticked. States shown: Sharing… /
  Ready (with the inbound number and “Sir, Zepto”) / rejected / not known / Clearing… /
  “Delivery line cleared” with the reason / clear failed. Ready is the server's decision.
- **Share automatically when an order is on its way** is a switch, off by default; turning
  it on asks for confirmation in a popup.
- **Clear delivery line** is shown whenever something may still be shared.
