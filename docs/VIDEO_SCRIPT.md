# GharSaathi — 4-minute demo video: shot list + voice-over

Recorded by the owner: phone (or Mac browser in mobile view) screen recording of
`http://localhost:3003`, plus his own voice. Total runtime target **4:00**.

## Before you hit record

- Run from `main`; backend and frontend both up; Zepto (and Swiggy, if linked) connected.
- Seed memory so recall has something to say: `make memory-seed` / `make memory-synth`
  (dry-run first, then `--apply`), then `make demo-check`.
- Default delivery address selected. Delivery line (Beta) set up in Settings, with saved
  directions for this address.
- A recent order (or the one you place in Scene 4) so tracking has a real status.
- A clear photo of a product pack (e.g. Amul butter, a shampoo bottle) on the phone.
- Silence notifications, set Do Not Disturb (allow calls if you will demo the rider line),
  battery/clock tidy. Mic permission granted for voice.
- Decide now: **demo tap → Cancel**, or **real COD order** (only if you really want it).

Legend: **DO** = what to tap / show on screen. **SAY (app)** = what you say or type to
GharSaathi. **VO** = your voice-over to the viewer.

---

## 0:00–0:15 — Hook

- **DO:** Open on the chat screen, idle. Optional: quick cut of you holding the phone.
- **VO:** "Hi, this is GharSaathi. Ghar ka saaman order karna simple lagta hai, but every
  time you're switching apps, comparing prices, remembering everyone's preferences, and
  then explaining the address to the rider. GharSaathi handles all of that in one natural
  Hinglish conversation. Aap bas bolo, it does the work. But every important decision,
  especially placing an order, still stays with you."

## 0:15–0:35 — Header + address first

- **DO:** Tap the address in the header; show the saved-address list; pick Home. Point at
  connected stores (Zepto, Swiggy Instamart).
- **VO:** "Pehle address. Every price, stock and delivery time depends on where you are,
  so GharSaathi starts with your saved delivery address — straight from your Zepto and
  Instamart accounts, linked with their own login. Hum kabhi password ya OTP nahi maangte."

## 0:35–1:05 — Memory: "Mujhe kya pasand hai?"

- **DO:** Tap mic. **SAY (app):** "Mujhe kya pasand hai?"
- **DO:** Let the short spoken answer play; Settings → **What GharSaathi knows** opens.
  Point at source badges (You told us / From history / Said in passing). Optionally edit
  one item.
- **VO:** "It remembers what my household actually told it — brands, pack sizes, what we
  avoid. Aur sab kuch screen par dikhta hai, with where it came from. Galat hai? Edit ya
  delete, ek tap."

## 1:05–1:45 — Express order: "2 Amul lassi order kar do"

- **DO:** Tap mic (or type). **SAY (app):** "2 Amul lassi order kar do."
- **DO:** Show the express **order card**: item, qty 2, store, bill with fees, payable
  amount, address, **Cash on delivery**, **Place order** button. Hold 2 s on the card.
- **DO:** Tap **Place order** →
  - *Demo:* tap **Cancel** instead, show card invalidated.
  - *Real COD (only if you choose):* tap **Place order**, wait for the store's
    confirmation message.
- **VO:** "One sentence, and it builds the exact basket and bill — deterministic maths,
  not AI guesses. Lekin order tabhi jaata hai jab main khud is card par 'Place order'
  tap karun. Bolke 'haan' kehna approval nahi hai. The card is tied to this exact basket,
  price and address, and expires in five minutes."
  - *If real:* "…and it says 'placed' only after Zepto confirms it."
  - *If demo:* "For this video I'll cancel — and that card is now dead."

## 1:45–2:15 — Comparison: "difference in price?"

- **DO:** **SAY (app):** "Zepto aur Instamart mein price ka difference kya hai?"
  (or "difference in price?")
- **DO:** Show the side-by-side comparison: price, pack size, delivery time, availability.
- **VO:** "Cheapest hamesha best nahi hota. It compares final payable, pack size,
  delivery time and stock across both stores — and it won't push extra items just to
  unlock a coupon unless that genuinely saves money."

## 2:15–2:40 — Photo of a pack → options

- **DO:** Tap 📎 → Take photo / Gallery → pick the pack photo. **SAY (app):** "Yeh wala
  mangwana hai."
- **DO:** Show the matched product options from the store catalogue.
- **VO:** "Naam yaad nahi? Just show it the pack. It reads the photo and finds the real
  listings — matching brand and size first."

## 2:40–3:00 — Tracking card + Delivery line ready

- **DO:** **SAY (app):** "Mera order kahan hai?"
- **DO:** Show the tracking card: status from the store, ETA, last updated, and the
  **Delivery line ready** indicator.
- **VO:** "Tracking comes straight from the store's own status — ETA bhi, last update
  bhi, koi made-up promise nahi. And the delivery line is ready for when the rider calls."

## 3:00–3:35 — Sarvam delivery line (rider guidance)

- **DO (pick one):**
  - *Forwarded call:* have someone call your phone as the "rider"; **decline** it
    (or press the side button twice). The forwarded call reaches the AI.
  - *Direct:* from a second phone, call **+91 80 6426 4240**.
- **DO:** Put it on speaker; let the AI greet the rider and give step-by-step directions
  from your saved directions (gate, floor, landmark). Ask as rider: "OTP kya hai?" — it
  refuses.
- **DO:** Switch to the app: show the **rider-call card** in chat (call summary / what
  the rider was told).
- **VO:** "Meeting mein hoon, rider call kar raha hai — I just decline. GharSaathi's
  delivery line, built on Sarvam voice, picks up, guides the rider in Hindi using my saved
  directions, and never shares OTPs or payment details. Baad mein app mein poora summary."

## 3:35–3:48 — Settings drawer

- **DO:** Open Settings: connected accounts, addresses, What GharSaathi knows, Delivery
  line (Beta), payments (COD), Sign out. Scroll once, close.
- **VO:** "Sab control ek jagah — linked stores, addresses, memory, delivery line.
  Nothing hidden."

## 3:48–4:00 — Safety close

- **DO:** Back to chat; end on the app logo/chat screen.
- **VO:** "GharSaathi kaam karta hai, faisla aap karte ho. Orders only on your tap,
  cash on delivery, no OTPs, no passwords, no surprise calls. Ghar ka saathi —
  GharSaathi."

---

## Recording tips

- Keep each voice prompt short; wait for the reply to finish before tapping.
- Record screen and VO separately if takes get messy; VO lines are timed for ~150 wpm.
- If a store is unreachable mid-take, keep it — the honest "Zepto abhi reach nahi ho
  raha" message is a good moment — and retake later if needed.
- If you placed a real order, mention it; if you cancelled, say so. Never claim an order
  was placed without the store's confirmation on screen.
- After recording: remove synthetic history
  (`make memory-synth ARGS="--remove --apply --i-understand-synthetic"`), paste the link
  into `VIDEO.md`.
