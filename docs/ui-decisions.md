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
