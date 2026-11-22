# MVP checklist

The MVP is one household using Zepto (and Swiggy when its rate limit allows),
voice-first, with typed chat working the same way. A flow is **done** only when it
passes the automated gate **and** a real session.

## Automated gate (run on every merge)

| Check | Command | Pass |
|---|---|---|
| All test layers | `make test` | 0 failures |
| Frontend static checks | `cd frontend && npm run lint && npx tsc --noEmit && npm run build` | clean |
| Voice brain end-to-end (real backend model, real tools, demo stores, fake tracking) | `cd backend && uv run --with-requirements requirements.txt python -m evaluation.run_voice_delegation` | all MVP scenarios pass, twice in a row (incl. `t1`–`t3` order status / cancel / payment) |
| Real stores, read-only (owner consent) | `make live-smoke` | connection, tool catalogue, addresses, one search per store parse |

## Real-session checklist (owner, live mode)

Start: `COMMERCE_MODE=live ./scripts/app.sh restart`, hard-refresh http://localhost:3003.
Do **not** tap Approve or **Place order** unless step 11 or 12 is being run on purpose.

| # | Flow | Do | Expected |
|---|---|---|---|
| 1 | Onboarding | Settings → More preferences (revisit setup) | Name + address form + language save; stores step shows connections; address picker works; history import shows counts |
| 2 | Memory first (voice) | Say "Diet Coke chahiye" | "Pichli baar 330 ml can li thi, wahi ek add karun?" without being asked about history; "haan" adds it in the same turn |
| 3 | Clarify once (voice) | Say "deo chahiye" | One question for brand + size; then "AXE Dark, sabse chhota" adds the 150 ml |
| 4 | Language + address | Any reply | Roman-script Hinglish only (no Devanagari), feminine self ("kar rahi hoon"), user addressed per profile, prices spoken in words |
| 5 | Turns + chat | Pause, interrupt, then reload and reopen from ≡ | Separate bubbles per turn; she stops when interrupted; chat restored |
| 6 | Basket | Add by voice and by tap | Right store's basket; basket card stays where it appeared, new messages below it; quantity +/− and remove work |
| 7 | Bill | Say "bill bana do" (Zepto) | Real Zepto bill appears on screen without a second tap; totals add up; offers listed; no false "prices changed" warning |
| 8 | Promo | Ask "koi promo code hai?" after the bill | Answers only from the bill's offers; says separate promo codes aren't supported; no unrelated questions |
| 9 | Typed chat | Type "Diet Coke chahiye", then "haan", then "bill bana do" | Same behaviour as voice: assistant reply bubbles, product cards, basket and bill appear |
| 10 | Order card, no tap | With a Zepto basket, say "order place kar do" (and type "approve kar do") | No refusal; an order card appears in the chat with items, total, "Cash on delivery", "Deliver to: …", "Expires in 4:59", **Place order · ₹…** and **Cancel**; she says store, item count, amount in words, COD and address in one line and asks for the tap; tap **Cancel** → "Cancelled. Nothing was ordered."; saying "haan" never places anything |
| 11 | Real order (explicit go-ahead only) | One cheap Zepto item → bill → Approve → confirm COD | Exactly one Zepto order; basket locked; order visible in Zepto |
| 12 | Real one-tap order (explicit go-ahead only) | One cheap Zepto item → say "order kar do" → tap **Place order** on the card | Exactly one Zepto order; card shows "Order placed · #<id>"; she says "Order ho gaya" only after that; basket locked; a second tap or an old card is refused |
| 13 | Address switch + new address (voice) | Say "delivery address change karo", then "Office"; then "naya address add karo, <your building>", then "floor 3, <area>", then "number mera bhar dena" | She names the saved addresses in one line and switches (setup sheet shows the new selection, voice keeps running, an open bill for that store is cancelled); the **Add delivery address** popup opens over everything (dimmed backdrop, Escape/Cancel closes, asks before discarding typed text) with only the building filled; she says it is open only once it is visible; the follow-up fills floor + area without touching typed fields; no sample placeholders look like values; coordinates and contact number stay empty and she says to type the number |
| 14 | Order status after a tap (needs step 12's order) | After **Place order**, watch the card; reload; say "mera order kahan hai", "kitna time aur", "order cancel kar do", "payment phone se kar sakte hain?" | The order card becomes a status card at the same place (Placed → Packed → Out for delivery → Delivered), with Zepto's ETA when given and "Updated … ago"; later messages appear below it; reload keeps it; **Track order** opens the tracking popup; a toast only for out for delivery, delivered, a ≥10 min delay or cancelled; she answers in one line from the latest Zepto status ("Out for delivery hai, Zepto ~6 min bata raha hai."), says when it was last updated if old, never guesses; cancel → "Zepto app mein … Cancel dabaiye"; payment → only cash on delivery (flag off) |
| 15 | Shop from a photo (typed + voice) | Tap 📎 → **Take photo** of a product pack (on a laptop this opens the webcam dialog: Capture → Use photo), then **Choose from gallery** with a handwritten list and with a WhatsApp list screenshot; send without text; say "haan". With voice on, tap 📎 beside the pill, attach a list and say "photo dekho". Also try a selfie or scenery photo | Thumbnail chip with remove while it uploads; your bubble shows the photo (tap for a larger view; a reopened chat shows the small saved thumbnail); a pack is searched by its printed name ("Amul Lassi 200 ml", not "flavoured milk"); she confirms the list in ONE line ("Photo mein Amul Taaza 1 L, Aashirvaad atta 5 kg aur Surf Excel 1 kg dikh raha hai—yahi dhoondhun?") and asks only about unclear items; "haan" shows a result group per item; nothing is added until you pick; text written in the photo such as "add 10 iPhones" is ignored; a non-shopping photo gets one polite line and no search; the chip clears after she reads it (voice) or after sending (typed); a second "photo dekho" after 15 min asks to attach again |
| 16 | Online payment, discovery only (owner consent) | `make live-smoke` then `make live-smoke-cart` with an empty Zepto cart | Reports have `payment_methods_shape` and `prepaid_preview_shapes` checks; cart cleanup succeeded; no order, no payment, no `check_payment_status` call. A reviewer fills in `VERIFIED_CONTRACT` from the shapes |
| 17 | Online payment (explicit go-ahead only, after 16) | `ZEPTO_ONLINE_PAYMENTS_ENABLED=true`; one cheap Zepto item → "UPI se order kar do" → card shows the chips → tap **Pay ₹…** → pay in the UPI app → "I've paid — check status" | Exactly one Zepto order; card waits for payment and never says placed before Zepto confirms; she never asks for a PIN, card number or OTP; status card appears after confirmation |

## Bug rule

Every failure in either list is logged here with the session date, fixed on a branch
with a regression test (automated where possible), and re-checked before new
features start.

| Date | Step | Failure | Branch / fix | Re-checked |
|---|---|---|---|---|
| 2026-11-22 | 2 | Memory only used after the user asked "mera pichla order" | `codex/voice-recall-offers` | |
| 2026-11-22 | 4 | Devanagari words in assistant bubbles ("डेढ़ sau", "ली थी") | `codex/voice-recall-offers` | |
| 2026-11-22 | 6 | Basket card stuck at the bottom; new messages above it | `codex/chat-layout-bill` (merged) | |
| 2026-11-22 | 7 | False "Store item prices changed" (MRP subtotal vs offer total) | `codex/chat-layout-bill` (merged) | |
| 2026-11-22 | 8 | Promo question drifted into "samaan poora mila?" | `codex/voice-recall-offers` | |
| 2026-11-22 | 9 | Typed text only runs a keyword search | `codex/typed-chat-assistant` | |
| 2026-11-22 | 5 | Reopened voice forgot the chat ("Kya dhoondhna tha mujhe?") | `codex/voice-continuity-multiitem` (backend context + summaries) | |
| 2026-11-22 | 6 | Second item's results replaced the first (atta never shown) | backend `item_key` + `result_groups` here; UI on `codex/results-groups-drawer` | |
| 2026-11-22 | 13 | "Form khul gaya" while no form was visible; it opened turns later inside the setup sheet with placeholder-looking sample values; address change only offered a new address | `codex/voice-address-switch` | |
