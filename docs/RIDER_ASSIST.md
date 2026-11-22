# Rider assist (beta)

When a delivery partner calls the owner and the owner doesn't pick up, the SIM
forwards the call to the owner's **Vobiz** number. Vobiz asks GharSaathi what to
do, GharSaathi streams the call audio to OpenAI Realtime, and an AI assistant
answers. It says it is an AI, answers from the owner's delivery notes and the
active order, and messages the owner in the app. It never shares an OTP, never
agrees to money, never changes the order and never claims to be the owner.

Status: **beta, off by default** (`RIDER_ASSIST_ENABLED=false`). Unit, module and
integration tests pass against fakes of Vobiz and OpenAI Realtime. It has not been
tried on a real Vobiz call yet.

## Call flow

```mermaid
sequenceDiagram
  participant R as Rider
  participant S as Owner's SIM (carrier)
  participant V as Vobiz (Bengaluru 080 number)
  participant B as GharSaathi backend (ap-south-1)
  participant O as OpenAI Realtime
  participant A as Owner's app
  R->>S: calls the owner
  S->>V: conditional forward (no answer / busy / unreachable)
  V->>B: POST /api/rider/vobiz/answer (CallUUID, From, To) + X-Vobiz-Signature-V3
  B->>B: check signature, flag, owner switch, number; remember the call
  B-->>V: <Stream bidirectional μ-law> wss://…/api/rider/stream?s=<call>.<signed token>, then <Redirect> after-stream
  V->>B: WebSocket: start (callId, streamId, mediaFormat audio/x-mulaw 8000)
  B->>O: wss /v1/realtime (audio/pcmu in and out, server VAD, 4 tools)
  O-->>B: greeting audio ("Namaste, main Moin ki AI assistant hoon — …")
  B-->>V: playAudio (μ-law 8 kHz, 40 ms frames)
  R->>V: "Gate pe hoon, flat number?"
  V->>B: media frames → input_audio_buffer.append (unchanged bytes)
  O->>B: get_delivery_notes / get_active_order / notify_owner
  B->>A: call summary saved; rider_called notification (in-app card + toast; browser push if on)
  O->>B: end_call (after a goodbye)
  B-->>V: checkpoint "end-call" → playedStream → stop
  V->>B: POST /api/rider/vobiz/after-stream (signed)
  B-->>V: <Hangup/> (or a spoken apology first, if the assistant never answered)
```

## Design

| Part | Where | What it does |
|---|---|---|
| Telephony adapter | `backend/app/rider/telephony.py` (interface), `backend/app/rider/vobiz.py` | Parses carrier stream events (`start`, `media`, `playedStream`, `clearedAudio`, `dtmf`). Formats `playAudio`, `checkpoint`, `clearAudio` and `stop`. Builds the answer XML, verifies webhook signatures and signs per-call stream URLs. Vobiz is the only adapter. The Exotel draft was removed. |
| Audio | `backend/app/rider/audio.py` | **μ-law 8 kHz passthrough** both ways. Vobiz streams `audio/x-mulaw;rate=8000` and Realtime takes and produces `audio/pcmu`, so no transcoding. If Vobiz ever reports L16 (8 or 16 kHz), it is resampled to Realtime's 24 kHz PCM, and Realtime's 24 kHz output is played back as L16/24000. Vobiz accepts that for playback. Outgoing audio goes out in 40 ms frames. |
| Bridge | `backend/app/rider/bridge.py` | One call. Waits for `start` and refuses a stream for another call. Opens Realtime, sends the greeting, then pumps audio both ways. On barge-in (`input_audio_buffer.speech_started`) it sends `clearAudio` and `conversation.item.truncate`. Runs the tools and ends after a `checkpoint`/`playedStream`, with a timeout. The cap is 3 min (wrap-up line 20 s before). A call with 20 s of no audio is treated as gone. |
| Tools (the only ones) | `backend/app/rider/tools.py` | `get_active_order`, `get_delivery_notes`, `notify_owner(summary, urgency)`, `end_call(reason)`. None of them can pay, cancel, change an order, read an OTP or make a call. Arguments are strictly validated, and `notify_owner` is capped at 3 per call. |
| Guards | `backend/app/rider/safety.py` | Every tool output and stored summary goes through `redact_secrets`. A code next to OTP, PIN, code or password becomes `[hidden]`. Store-provided status text also loses bare 4–8 digit codes. Delivery notes refuse OTPs, PINs, passwords, payment details and links. Rider requests for OTPs, money or order changes are flagged into the owner's summary. |
| Data | `backend/app/rider/orders.py` | Orders come only from GharSaathi's own records: provider-confirmed Zepto checkout attempts from the last 12 h, plus their basket. Status comes from the local tracking monitor, and the address from the saved-address snapshot (label + short line). **No store is called during a call.** |
| Prompt | `backend/app/rider/prompts.py` (`rider-1.0.0`) | The owner's first name comes from the profile, the language from the delivery notes. It always identifies as an AI. Directions are given only from the data, and it messages the owner instead of guessing. The main voice assistant prompt is unchanged. |
| Owner updates | `backend/app/rider/store.py`, `backend/app/rider/service.py` | Saves a short summary per call: time, masked caller, ≤120 chars of what the rider said, outcome, and answered from notes/order. **No audio and no transcript are kept.** The owner can delete calls. The first save of a call sends one `rider_called` notification through the generic notification service, deduped per call. Users can turn this kind off. |
| App | `frontend/app/rider-assist-settings.tsx`, `rider-calls.tsx`, `rider-assist.mjs` | Settings → **Rider assist (Beta)**: a switch (it shows the server flag and setup state), a delivery notes popup, a setup checklist, and recent calls with delete. The chat polls every 30 s while visible and shows a toast plus a card such as "Rider called · 8:24 am · 'Gate pe hoon, flat number?' — answered from your notes". |

### Security

- **Answer and after-stream webhooks** must carry a valid `X-Vobiz-Signature-V3`
  (or `-V2`). That is HMAC-SHA256 of `base URL + "." + nonce` (V2: no `.`), keyed with
  `VOBIZ_AUTH_TOKEN`, compared in constant time. The base URL is
  `RIDER_ASSIST_PUBLIC_URL` + the path, never what the proxy reports. A missing
  signature is refused (403).
- **Stream**: `extraHeaders` cannot authenticate the socket, so the answer URL
  issues `wss://<host>/api/rider/stream?s=<CallUUID>.<expiry>x<HMAC>`. The HMAC is
  over the CallUUID and expiry with `RIDER_ASSIST_STREAM_SECRET`, and the URL is
  valid for 120 s. It is checked before the WebSocket is accepted. The socket is
  used once per answered call, and the `start.callId` must match the signed call.
  The token is redacted from access and WebSocket logs.
- Off unless `RIDER_ASSIST_ENABLED=true` *and* the owner's own switch is on. Only
  the single owner (`RIDER_ASSIST_OWNER_USER_ID`) is served. Calls to a number
  other than `RIDER_ASSIST_NUMBER` (if set) are refused. At most 2 calls run at once.
- Caller ID does not prove a Zepto rider. The assistant first asks whether the call
  is about a delivery, and only then reads orders or notes.
- Calls are never placed. No Vobiz configuration is changed by code. The only
  Vobiz API call is `make vobiz-check` (read-only).

### Not yet

- **Single owner.** Multi-user needs a number→user mapping (one Vobiz number per
  household, or a DTMF/phone lookup) and per-user stream secrets.
- **Tested against fakes only.** Before relying on it, confirm that Vobiz really
  sends signature headers for your application's answer URL. Vobiz notes they are
  emitted only when the URL has auth credentials configured. GharSaathi refuses
  unsigned requests.

## Owner setup

1. **Vobiz account + India KYC**
   1. Sign up at <https://console.vobiz.ai/auth/signup>.
   2. Complete the India KYC the console asks for. Trial numbers are
      outbound-only; inbound needs a fully provisioned number
      (<https://www.vobiz.ai/docs/faq/trial-inbound>).
   3. In **Settings → API**, copy the **Auth ID** and **Auth Token**.
2. **Buy a Bengaluru 080 number** in the console (Numbers). It must have voice
   capability. You can confirm it read-only:

   ```bash
   # .env: VOBIZ_AUTH_ID=…  VOBIZ_AUTH_TOKEN=…
   make vobiz-check   # lists your numbers masked (•••• 899), status and whether an answer app is set
   ```

3. **Backend settings** (`.env` locally, Secrets Manager in AWS via `scripts/aws-secrets.sh`):

   ```bash
   RIDER_ASSIST_ENABLED=true
   RIDER_ASSIST_PROVIDER=vobiz
   RIDER_ASSIST_OWNER_USER_ID=user_…        # your Clerk user id
   RIDER_ASSIST_PUBLIC_URL=https://rider.<your-domain>   # exactly what Vobiz will call
   RIDER_ASSIST_STREAM_SECRET=…             # python -c "import secrets; print(secrets.token_urlsafe(32))"
   RIDER_ASSIST_NUMBER=+9180XXXXXXXX        # your Vobiz number
   VOBIZ_AUTH_ID=…
   VOBIZ_AUTH_TOKEN=…
   ```

4. **Create a Vobiz Application** (console → Applications, or
   `POST /api/v1/Account/{auth_id}/Application/`;
   <https://www.vobiz.ai/docs/applications/create-application>):
   - Answer URL: `https://rider.<your-domain>/api/rider/vobiz/answer`, method **POST**.
   - Set auth credentials on the URL as the console offers, so Vobiz signs the
     webhooks (see Security).
   - Optional: a fallback answer URL that just says "please call again".
   - Assign the 080 number to this application.
5. **Turn it on in the app**: open Settings → Rider assist (Beta), fill in your
   delivery notes (landmark, gate/tower/flat directions, what to do if nobody
   opens, and the language for riders), then turn the switch on.
6. **SIM conditional call forwarding**. Dial these from the phone, using your Vobiz
   number in place of `<number>`. They are GSM codes most Indian carriers accept
   (Jio, Airtel, Vi, BSNL). Some carriers want the number with `+91` or a leading
   `0`, and some charge for forwarded minutes.

   | When | Turn on | Turn off |
   |---|---|---|
   | No answer | `**61*<number>#` | `##61#` |
   | Busy | `**67*<number>#` | `##67#` |
   | Unreachable / switched off | `**62*<number>#` | `##62#` |
   | Cancel **all** forwarding | — | `##002#` |

   Test it: have someone call you, let it ring out, and you should hear the
   assistant. The call then shows up in GharSaathi.

### Local testing through a tunnel (development only)

Vobiz needs a public HTTPS/WSS address. Neither tool is installed on this Mac;
install one yourself (not system-wide is fine):

- **cloudflared**: `brew install cloudflared`, then `cloudflared tunnel --url http://localhost:8000`.
- **ngrok**: `brew install ngrok`, `ngrok config add-authtoken …`, then `ngrok http 8000`.

Set `RIDER_ASSIST_PUBLIC_URL` to the tunnel's `https://…` origin, restart the
backend, and put `<origin>/api/rider/vobiz/answer` in the Vobiz application.
**A tunnel routes Indian call media through servers outside India. Use it only
for development with test calls you make yourself, never for real riders.**

## Production hosting and compliance (TRAI)

- **Media anchoring.** Indian call media must stay on servers in India. In
  production the rider endpoints (`/api/rider/vobiz/*` and `/api/rider/stream`) must run in
  **AWS `ap-south-1` (Mumbai)**. The AWS template does this:
  `RIDER_ENABLED=true scripts/deploy-aws.sh deploy` creates `rider.<domain>` straight
  on the Mumbai ALB (not CloudFront), forwarding only those two paths
  (`infra/aws/README.md`).
- **Mumbai hosting alone is not enough.** The bridge sends the call audio to
  **OpenAI Realtime**, which processes it on OpenAI's servers. Before real calls,
  check that the OpenAI project's data residency covers India for Realtime audio,
  or switch to an India-hosted speech model. Get advice on whether this setup
  satisfies TRAI and DPDP for your use. GharSaathi does not claim it does.
- Vobiz's own OpenAI Realtime integration
  (<https://www.vobiz.ai/integrations/openai-realtime/>, docs at
  <https://www.vobiz.ai/docs/integrations/openai-realtime>) sends SIP media to
  OpenAI directly. GharSaathi deliberately uses the `<Stream>` WebSocket instead,
  so the telephony media terminates on our Mumbai server, the tools and guards
  run in our code, and no SIP trunk to OpenAI is needed.

## Costs and privacy

- **Costs**: the Vobiz number's monthly rent, plus per-minute inbound and stream
  charges (see the console). The carrier may charge for forwarded minutes, and
  OpenAI Realtime bills audio minutes. The 3-minute cap bounds each call.
- **Recording is off.** `<Stream>` does not record, GharSaathi stores no audio and
  no transcript, and OpenAI Realtime is opened without storage options. Only the
  short summary is kept, and you can delete it in Settings.
- The browser notification (`rider_called`) carries only the time, ≤60 characters
  of what the rider said (codes hidden) and the outcome. It never includes the
  address or your notes. It can be turned off in Settings → Notifications.
- **No unsolicited calls to the owner.** GharSaathi never calls you; you get an
  in-app message and, if enabled, a browser notification.

## Vobiz references (read 2026-11-22)

| Fact used | Source |
|---|---|
| Answer URL returns XML; inbound webhook params `CallUUID`, `From`, `To`, `Direction`; reply within 10 s | <https://www.vobiz.ai/docs/xml/overview/how-it-works>, <https://www.vobiz.ai/docs/applications> |
| `<Stream bidirectional keepCallAlive contentType audioTrack streamTimeout>`; URL is the element text | <https://www.vobiz.ai/docs/xml/stream>, <https://www.vobiz.ai/docs/xml/stream/initiate> |
| Inbound formats: μ-law 8 kHz, L16 8/16 kHz (24 kHz not inbound); outbound: μ-law 8 kHz, L16 8/16/24 kHz | <https://www.vobiz.ai/docs/xml/stream/audio-formats>, <https://www.vobiz.ai/docs/concepts/streaming-websockets> |
| Events in: `start` (`start.callId`, `start.streamId`, `start.mediaFormat`), `media` (20 ms frames, `media.payload`), `playedStream` (`name`), `clearedAudio`, `dtmf`; no inbound `stop` (socket close ends it). Commands out: `playAudio` (`media.contentType`, `media.sampleRate`, `media.payload`), `checkpoint` (`name`), `clearAudio`, `stop` (next XML runs) | <https://www.vobiz.ai/docs/xml/stream/stream-events> |
| Chunking: ~20–60 ms recommended for barge-in, not a protocol rule (20 ms μ-law = 160 bytes); `playedStream` is conditional | <https://www.vobiz.ai/docs/concepts/streaming-websockets>, <https://www.vobiz.ai/docs/xml/stream/stream-events> |
| `extra_headers` arrive in `start` as a non-JSON `{X-VH-key: value}` string and cannot authenticate the socket; put the secret in the URL | <https://www.vobiz.ai/docs/xml/stream/dtmf>, <https://www.vobiz.ai/docs/integrations/deepgram> |
| `<Redirect>` fetches new XML after the stream; `<Speak language="hi-IN">`; `<Hangup/>` | <https://www.vobiz.ai/docs/xml/redirect>, <https://www.vobiz.ai/docs/xml/speak>, <https://www.vobiz.ai/docs/xml/hangup> |
| Webhook signatures `X-Vobiz-Signature-V2/V3` (+ `-Nonce`), HMAC-SHA256 with the auth token over base URL (+ `.`) + nonce; only sent when the URL has auth credentials configured | <https://www.vobiz.ai/docs/concepts/validating-callbacks>, <https://www.vobiz.ai/docs/concepts/callbacks> |
| REST auth `X-Auth-ID` / `X-Auth-Token`; list owned numbers `GET https://api.vobiz.ai/api/v1/Account/{auth_id}/numbers?page=&per_page=` → `items[].e164`, `status`, `capabilities`, `application_id` | <https://www.vobiz.ai/llms.txt>, <https://www.vobiz.ai/docs/account-phone-number/list-account-phone-numbers> |
| Docs index / docs MCP (read-only, used to search `extra_headers`) | <https://www.vobiz.ai/docs/llms.txt>, <https://www.vobiz.ai/docs/mcp> |

OpenAI: Realtime accepts `audio/pcmu` (G.711 μ-law) as input and output format
(`RealtimeAudioConfig`, <https://developers.openai.com/api/reference/resources/realtime>).
WebSocket connection, `session.update`, `input_audio_buffer.speech_started`,
`conversation.item.truncate` and function calling are covered in
<https://developers.openai.com/api/docs/guides/realtime-conversations>.

## Tests and evaluation

- Unit: `backend/tests/unit/test_rider_assist.py` covers the Vobiz protocol,
  signatures, stream tokens, answer XML, chunking, resampling, OTP/charge guards,
  notes validation and the prompt rules. `backend/tests/unit/test_push_messages.py`
  covers the `rider_called` message.
- Module: `backend/tests/module/test_rider_bridge.py` runs a fake Vobiz stream and a
  fake Realtime. It checks the AI greeting, answers from the notes, the owner
  notification, that no OTP-like string reaches the model, barge-in, hang-up, the
  cap and L16 resampling. `test_rider_api.py` covers the webhooks and settings, and
  `test_failure_modes_rider.py` covers FM-R01…FM-R07 in
  [FAILURE_MODES.md](FAILURE_MODES.md).
- Integration: `backend/tests/integration/test_rider_assist.py` (PostgreSQL schema).
- Frontend: `frontend/tests/unit/rider-assist.test.mjs`, plus `FM-R03` in `failure-modes.test.mjs`.
- Prompt eval (OpenAI only, demo data, DNS limited to the OpenAI host). It runs the
  prompt as text through Responses with `LIVE_BACKEND_MODEL`, a proxy for the
  Realtime audio model:

  ```bash
  cd backend && uv run --with-requirements requirements.txt python -m evaluation.run_rider_prompt --runs 2
  ```

  Scenarios: the rider asks for the flat number, the OTP, online payment plus an
  extra charge, and a non-delivery caller. 2026-11-22, `rider-1.0.0` on
  `gpt-6-luna`: 8/8 passed (`backend/evaluation/rider_prompt_report.json`).
