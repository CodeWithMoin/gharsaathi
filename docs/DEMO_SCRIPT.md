# GharSaathi demo script (draft)

Five short scenes, about 6 minutes in total. Run from `main` with live stores, or with
`make start-demo` for the static catalogue. Before the demo:

```bash
make memory-seed  ARGS="--file .run/demo-memory.yaml --dry-run"   # then --apply
make memory-synth ARGS="--file .run/demo-memory.yaml --months 12 --seed 42 --dry-run"   # then --apply (+ --i-understand-synthetic in live mode)
make demo-check
```

Markers: **[express]** needs `codex/express-order` (merged), **[chips]** needs
`codex/quick-actions-ui` (merged). Steps without a marker work on this branch alone.

## 1. Memory recall: "she knows my home" (≈90 s)

1. Say: *"Mere baare mein kya pata hai?"*
   - Expected: one or two sentences, food rules first (diet, what to avoid), then usual
     buys, ending "baaki screen par hai"; Settings → **What GharSaathi knows** opens.
2. On screen, point at the groups and source badges: **You told us** (seeded answers),
   **From demo history** (synthetic orders), **Said in passing**.
3. Edit one usual buy (e.g. milk quantity) and delete one avoid item; say *"ab kya yaad
   hai?"* and show it changed.
4. Say: *"Cold drink dikhao."* Only diet/zero-sugar options appear; the summary of the
   search mentions that the household's rule was applied.

## 2. Express order: one sentence to the order card (≈75 s) [express]

1. Say: *"Usual doodh aur do Diet Coke order kar do."*
   - Expected: the remembered Amul Taaza 500 ml (seeded usual buy) is picked without a
     question, Diet Coke quantity 2, Zepto bill, order card with **Place order**.
2. Point out: nothing is ordered until the tap. Tap **Cancel** (or place it if this is a real
   run the owner approved).
3. [chips] Tap a quick-action chip (e.g. "Usual milk") to show the same path from a tap.

## 3. Shop from a photo (≈60 s)

1. Attach a photo of a handwritten list (📎 → Take photo / Choose from gallery).
2. Say: *"Is list se mangao."*
   - Expected: items read back, each searched; remembered brands chosen; one combined
     question for anything ambiguous.

## 4. Tracking (≈45 s)

1. After a placed order (or with a recent one): *"Mera order kahan hai?"*
   - Expected: one line from the store's own status (ETA if given, "last updated" if stale);
     the status card on screen. Synthetic demo history never appears here.

## 5. Honesty on failure (≈45 s)

1. Turn off the network for one store (or use the failure-mode harness) and ask for an item.
   - Expected: "Zepto abhi reach nahi ho raha" — never "not found", never a made-up price.
2. Ask *"order ho gaya?"* for a card that was not tapped.
   - Expected: it says the order is not placed yet and points to **Place order**.

## Notes for the presenter

- Synthetic history is clearly labelled in the app; say so if asked. Remove it after the demo:
  `make memory-synth ARGS="--remove --apply --i-understand-synthetic"`.
- Seeded answers stay until removed: `make memory-seed ARGS="--remove-seeded --apply"`.
