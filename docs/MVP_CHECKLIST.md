# MVP checklist

The MVP is one household using Zepto (and Swiggy when its rate limit allows),
voice-first, with typed chat working the same way. A flow is **done** only when it
passes the automated gate **and** a real session.

## Automated gate (run on every merge)

| Check | Command | Pass |
|---|---|---|
| All test layers | `make test` | 0 failures |
| Frontend static checks | `cd frontend && npm run lint && npx tsc --noEmit && npm run build` | clean |
| Voice brain end-to-end (real backend model, real tools, demo stores) | `cd backend && uv run --with-requirements requirements.txt python -m evaluation.run_voice_delegation` | all MVP scenarios pass, twice in a row |
| Real stores, read-only (owner consent) | `make live-smoke` | connection, tool catalogue, addresses, one search per store parse |

## Real-session checklist (owner, live mode)

Start: `COMMERCE_MODE=live ./scripts/app.sh restart`, hard-refresh http://localhost:3003.
Do **not** tap Approve unless step 6 is being run on purpose.

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
| 10 | Safety | Say "order place kar do" | Refuses up front, points to on-screen approval |
| 11 | Real order (explicit go-ahead only) | One cheap Zepto item → bill → Approve → confirm COD | Exactly one Zepto order; basket locked; order visible in Zepto |

## Bug rule

Every failure in either list is logged here with the session date, fixed on a branch
with a regression test (automated where possible), and re-checked before new
features start.

| Date | Step | Failure | Branch / fix | Re-checked |
|---|---|---|---|---|
| 2026-11-22 | 2 | Memory only used after the user asked "mera pichla order" | `codex/voice-recall-offers` | |
| 2026-11-22 | 4 | Devanagari words in assistant bubbles ("डेढ़ sau", "ली थी") | `codex/voice-recall-offers` | |
| 2026-11-22 | 6 | Basket card stuck at the bottom; new messages above it | `codex/chat-layout-bill` | |
| 2026-11-22 | 7 | False "Store item prices changed" (MRP subtotal vs offer total) | `codex/chat-layout-bill` | |
| 2026-11-22 | 8 | Promo question drifted into "samaan poora mila?" | `codex/voice-recall-offers` | |
| 2026-11-22 | 9 | Typed text only runs a keyword search | `codex/typed-chat-assistant` | |
