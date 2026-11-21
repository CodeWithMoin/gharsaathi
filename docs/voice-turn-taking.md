# Voice turn-taking on GPT-Live

Status: options built on `codex/smart-turn-options` and measured offline plus in 10 live
WebSocket sessions. **Not yet verified in a real browser session over WebRTC** (see
"Unverified").

## Problem

In real Hinglish sessions the assistant talks over the user and doesn't stop when
interrupted. Users who pause mid-sentence ("haan… woh… Dark wala") get cut off.
`main` sends GPT-Live (`gpt-live-1.1`) no turn-taking configuration at all.

## What the API actually offers (official OpenAI docs only)

**GPT-Live has no configurable turn detection.** The full session schema for
`POST /v1/live/sessions` (WebRTC) is `model`, `audio.output.voice`, `client`,
`delegation`, `idle_timeout_s`, `input`, `instructions`, `safety_identifier`, `store`,
`triggers` and `wake_policy`. None of these is `turn_detection`, a VAD threshold,
`eagerness`, `create_response` or `interrupt_response`.
([Primary WebSocket reference, `session.start`](https://developers.openai.com/api/reference/resources/live/primary-websocket);
the same `MediaSessionConfig` ships in the official `openai` Python SDK 3.57.0,
`openai/types/live/media_session_config_param.py`.)

| Control | GPT-Live (`gpt-live-1.1`) | Source |
|---|---|---|
| `server_vad` / `semantic_vad`, `eagerness`, `threshold`, `silence_duration_ms` | **Not available.** These are Realtime API fields (`session.audio.input.turn_detection`). | [Realtime VAD guide](https://developers.openai.com/api/docs/guides/realtime-vad) |
| `create_response: false` + `interrupt_response: true` (VAD detects speech, app decides when to respond) | **Not available.** Realtime only. | [Realtime conversations](https://developers.openai.com/api/docs/guides/realtime-conversations) ("keep VAD mode enabled, but … manually decide when a response is generated") |
| Manual `input_audio_buffer.commit` + `response.create` | **Not available.** The migration guide says: "Stream audio continuously. GPT-Live decides when to speak; remove manual audio commits and voice-turn triggers." In GPT-Live, `response.create` only starts or continues *delegated Responses work*. | [Migrate to GPT-Live](https://developers.openai.com/api/docs/guides/live-migration) |
| `response.cancel`, `output_audio_buffer.clear`, `conversation.item.truncate` | **Not available.** These are Realtime events (push-to-talk and interruption recipes). The Live client event list is: `session.start/update/close`, `session.input_audio.append/mute/unmute`, `session.output_audio.fixture.play`, `session.instructions/thinking/commentary.append`, `decision.*`, `response.item.create`, `response.create`. | [Realtime conversations](https://developers.openai.com/api/docs/guides/realtime-conversations); [Live primary WebSocket](https://developers.openai.com/api/reference/resources/live/primary-websocket) |
| How GPT-Live takes turns | Full duplex: it "can speak at the same time as the user. Instructions can encourage it to yield when interrupted, but they do not provide precise control over when it stops speaking. The model may keep speaking briefly after an interruption." You steer it with an `Interruption policy`, `Backchannel policy` and patience text in the Live instructions. | [Prompting GPT-Live → Interruptions, Patience](https://developers.openai.com/api/docs/guides/live-prompting) |
| Stop audio quickly | "If your application needs to block model audio, control playback at the client … Temporarily mute or drop the output, discard locally queued audio, and send the corrective instruction." `session.instructions.append` "can interrupt speech in progress". Discarded speech stays in the model's context. | [Server-side controls → Redirect / Control playback](https://developers.openai.com/api/docs/guides/voice-server-controls?api=live) |
| `session.input_audio.mute` | Stops the model hearing the mic. It "does not mute model output or cancel delegated work". | [Managing GPT-Live sessions](https://developers.openai.com/api/docs/guides/live-conversations) |
| `wake_policy.vad_threshold` | Only controls *resuming from standby*. The SDK says it "is independent of active turn detection". | [Managing idle sessions](https://developers.openai.com/api/docs/guides/live-conversations#managing-idle-sessions) |
| End-of-speech event | None. "GPT-Live has no corresponding event marking the end of each spoken response. Track playback in your client." | [Migrate to GPT-Live](https://developers.openai.com/api/docs/guides/live-migration) |

What this means:

- **Option A** can only use the instructions (interruption, backchannel and patience
  policy). There are no numbers to tune.
- **Option B** can't make GPT-Live wait or commit a turn. A client-side model can only
  decide when the *user hears* the assistant: it mutes playback while the user still
  has the floor, sends `session.instructions.append` to make the model yield, and
  unmutes when the user's turn is complete.
- **Option C (hybrid) isn't possible on GPT-Live.** The server can't detect speech
  for interruptions without also choosing when to respond. It only works on the
  Realtime API: `turn_detection: {type: "server_vad" | "semantic_vad",
  create_response: false, interrupt_response: true}`, then smart-turn sends
  `response.create`. That would mean moving off GPT-Live and its Responses
  delegation, so it isn't built. Its end-of-turn decisions would be the same as
  option B's in the tables below.

## Options implemented

One backend setting, `VOICE_TURN_TAKING` (in `.env`), defaults to `native`. The
`POST /api/voice/session` response now includes `turn_taking: {mode, yield_instruction?}`,
and the browser follows it.

| Mode | Server (`backend/app/voice/turn_taking.py`) | Browser (`frontend/app/voice-panel.tsx`) |
|---|---|---|
| `native` (default) | Unchanged `LIVE_INSTRUCTIONS`. | Unchanged, except the mic now explicitly requests `echoCancellation`, `noiseSuppression` and `autoGainControl` (already the browser defaults). |
| `tuned` (A) | Appends a `# Turn-taking` block: stop right away when interrupted, treat Hinglish fillers ("haan…", "woh…", "matlab…", "ek minute", trailing "aur…/ya phir…") as still thinking, no backchannels. `prompts.py` and `VOICE_PROMPT_VERSION` aren't touched. | Same as native. |
| `smart_turn` (B) | Same instructions as `tuned`, plus the session reports `yield_instruction`. | `SmartTurnController` (`smart-turn.ts`) taps the echo-cancelled mic and the assistant's remote audio level. It runs the turn gate (`smart-turn-policy.mjs`, shared with the eval). |

How the `smart_turn` gate works:

1. **User speech starts.** The mic must stay above 0.012 RMS for 80 ms. While the
   assistant is audible it must also exceed `max(0.03, 0.4 × assistant level)` for
   200 ms. This echo guard stops the assistant's own voice from counting as barge-in.
   Playback is then muted locally (**hold**).
2. If the assistant is audible at that moment, or starts talking while the turn is
   open, the browser sends one `session.instructions.append` with the yield
   instruction (**barge-in**). A rejected yield is ignored rather than shown as a
   voice error.
3. **End of turn.** After 200 ms of silence the browser runs the model every 200 ms
   on the last 4 s (right-aligned, peak-normalised like `smart_turn/infer.py`). Two
   checks in a row with `p_complete ≥ 0.7` release the turn; so does 1.6 s of
   silence. Releasing unmutes playback.
4. **Model loading.** The model is lazy-loaded only for `smart_turn` sessions, from
   `/models/smart_turn_hinglish_4s-39736bc2.onnx` (self-hosted, immutable cache
   headers), with ONNX Runtime Web WASM from `/ort/`. The browser checks the
   SHA-256 with `crypto.subtle` before creating the session. If anything fails, it
   unmutes and keeps the `tuned` session, so it falls back to **A**. The fallback
   message uses feminine forms ("…main normal tarah sun rahi hoon"). The two existing
   masculine status strings ("kar raha hoon", "bata raha hoon") are now feminine.

The model binary isn't committed because git LFS isn't set up. Get it with
`cd frontend && npm run fetch:smart-turn-model`. That downloads the pinned commit
`64eafecd…/runs/smart_turn_4s.onnx` (or copies `SMART_TURN_MODEL_SOURCE`) and
refuses a hash mismatch. `npm run dev/build` copy the ORT WASM assets automatically.

## Evaluation

Harness: `backend/evaluation/run_turn_taking.py` (results in `turn_taking_report.json`).

- **Clips.** macOS `say` + ffmpeg in 3 voices: Rishi and Aman (en-IN, romanised) and
  Lekha (hi-IN, Devanagari). There are 6 paused utterances, each with two
  mid-sentence pauses of 0.6–1.4 s:
  - 12 "clause" pauses after a phrase that is complete on its own, such as "Mujhe AXE
    deo chahiye … haan, woh … Dark Temptation wala"
  - 24 "hesitation" pauses after a filler or unfinished phrase

  There are also 4 complete requests (30 clips in all). Barge-in clips mix Rishi's
  "Ruko ruko, Dark wala chahiye" at 2.0 s over a female assistant answer, with
  echo leakage at −30, −20 and −12 dB, plus echo-only clips.
- **Offline (B).** `frontend/scripts/eval-smart-turn.mjs` streams the clips in 40 ms
  frames through the *same* gate code as the browser, with `onnxruntime-web` (WASM,
  1 thread). Model inference time is added to the decision time. Energy-silence
  endpointers are included as reference proxies; they are not GPT-Live.
- **Live (native and A).** 10 sessions to `gpt-live-1.1` over the primary WebSocket,
  with input paced in real time. Output audio goes through a simulated local player,
  and "assistant onset" is the first 60 ms above −34 dBFS. Client delegation results
  are never returned, and no tools or commerce calls are made.

Re-run with:
`cd backend && uv run --with-requirements requirements.txt python -m evaluation.run_turn_taking [--reuse-clips] [--live]`

### End of turn

| Option | Clips with a false end of turn | Hesitation pauses cut | Clause pauses cut | End-of-turn latency, median / p90 |
|---|---|---|---|---|
| **native**, live (n=3 paused clips) | **0/3** | 0/5 | 0/1 | **2701 ms median** (2.2, 2.7, 3.7 s) |
| **A tuned**, live (n=3) | **1/3**: "Ek second, dekh rahi hoon" at 4.78 s, inside the 3.86–5.06 s pause after "haan, woh" | 1/5 | 0/1 | 3161 and 3360 ms (2 clips not cut) |
| **B smart_turn default** (thr 0.7, 2 checks), offline (n=30) | 12/30 (16 events) | 8/24 | 8/12 | 1084 / 1600 ms |
| B thr 0.5, 1 check | 19/30 | 11/24 | 12/12 | 682 / 881 ms |
| B thr 0.7, 1 check | 19/30 | 10/24 | 12/12 | 883 / 1580 ms |
| B thr 0.5, 2 checks | 17/30 | 11/24 | 10/12 | 881 / 1600 ms |
| C hybrid | Not available on GPT-Live; its decisions would match B | — | — | — |
| proxy: energy silence 500 ms | 18/30 | 24/24 | 12/12 | 520 ms |
| proxy: energy silence 800 ms | 18/30 | 21/24 | 9/12 | 800 ms |
| proxy: energy silence 1200 ms | 9/30 | 9/24 | 0/12 | 1200 ms |

Smart-turn inference in WASM (Node, 1 thread): median 82.7 ms, p90 84.1 ms (1,034 runs).

The B row is the gate on its own. In a `smart_turn` session the user only hears the
assistant during a pause if **both** GPT-Live chooses to speak **and** the gate has
released. For the one live A cut-off (`axe_dark-rishi`), the default gate held both
pauses on the same audio. That interjection would have been muted and a yield sent.
In all three live clips the gate released (0.46–1.58 s) well before GPT-Live started
replying (2.2–3.7 s), so the observed replies would not have lost their opening words.

### Barge-in (does assistant audio stop?)

| Option | Result |
|---|---|
| **native**, live | The assistant kept talking for **0 ms** after an interruption 1.0 s into its reply (the short acknowledgement had already ended). Interrupted 0.3 s in, it kept talking for **774 ms**. It finished "Ek second, dekh rahi hoon." and then went on to "AXE Dark Temptation 150 ml, do sau rupaye," — a price with no tool result. |
| **A tuned**, live | Kept talking for **335 ms** (interrupted 1.0 s in) and **630 ms** (0.3 s in). |
| **B smart_turn**, offline | Local playback muted **200 ms** after the interruption at all three echo levels (−30, −20, −12 dB), and a yield was sent. Echo-only clips: **0/3 false barge-ins**. |

## Recommendation

1. **Ship `tuned` (A) as the next default once someone has listened to it in the
   browser.** It costs nothing and needs no client code. It didn't clearly beat
   `native` in this tiny live sample (n=3 + 2; one extra interjection, slightly
   shorter spill-over after barge-in). GPT-Live's own patience was already high:
   2.2–3.7 s before replying, with no cut-offs on native.
2. **Try `smart_turn` (B) behind the flag for real browser sessions.** The user's
   symptoms match acoustic echo or slow yielding, which the WebSocket test can't
   reproduce. B is the only option that stops what the user *hears* within about
   200 ms, regardless of how long GPT-Live keeps generating. Its echo guard rejected
   −12 dB leakage, and it falls back to A safely. Its own end-of-turn judgement on
   synthetic speech is only moderately better than a 1.2 s silence timer on
   hesitations (8/24 vs 9/24), and worse on clause pauses. So treat it as a playback
   gate on top of GPT-Live, not a replacement.
3. **Don't build C** unless the product moves to the Realtime API, which has the
   `create_response`/`interrupt_response` controls but loses GPT-Live delegation.
4. **Separate prompt finding (for the `codex/hinglish-replies` owner).** When
   interrupted before any tool result, native GPT-Live said "AXE Dark Temptation
   150 ml, do sau rupaye" — the example line from `LIVE_INSTRUCTIONS`. The prompt
   example can leak as an unverified price.

## Unverified / limitations

- Nothing has been tried in a real browser over WebRTC. That covers AEC quality, the
  analyser level of the remote track, Safari's `ScriptProcessorNode`, muting the
  `<audio>` element, and whether `session.instructions.append` over the data channel
  makes GPT-Live stop sooner than the 0.6–0.8 s measured above.
- Clips are TTS, not real Hinglish speakers. TTS fillers have falling, "final"
  prosody, which probably hurts smart-turn compared with real hesitations. The model
  card reports 0.977 AUC on Hindi.
- Live sessions ran before `main`'s prompt 1.8.0 (few-shot examples) was merged; they used
  `LIVE_INSTRUCTIONS` 1.6.0 and should be re-run.
- Live numbers come from n=3 end-of-turn and n=2 barge-in sessions per mode over
  WebSocket, with client delegation never answered. They show direction only. Live
  onset includes network and player-simulation timing.
- Holding playback means any assistant speech the gate mutes is lost to the user but
  stays in the model's context (documented GPT-Live behaviour). If GPT-Live ever
  replies before the gate releases, the first words of the reply are clipped. The
  1.6 s silence timeout bounds this.
- Echo-guard numbers (0.012 / 0.03 / 0.4×) are heuristics that need tuning on
  devices.
