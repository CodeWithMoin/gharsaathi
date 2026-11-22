# Inbound delivery-assist line

The Sarvam number `+918064264240` routes inbound calls to committed agent
version 2. The agent behaves as Moin's household shopping/delivery assistant.
“Sir, Zepto”, “Sir, Swiggy”, and equivalent delivery introductions start the
delivery flow. Other callers are offered a transfer to Moin.

Before a controlled hackathon demo, an authenticated user reviews and activates
one delivery context. FastAPI sends the exact recipient, address and landmark
to Sarvam's documented inbound-deployment cohort stream endpoint for one
server-configured demo caller number. The browser never supplies a phone
number, sees the Voice Agents API key, or places a call.

Unknown callers can reach the agent but receive no address. Address disclosure
requires both an approved caller match and an explicit delivery confirmation.
The agent never requests an OTP, payment or tip and never exposes Moin's phone
number; transfers happen through Sarvam only after caller confirmation.

Automated tests mock every Sarvam response. They never dial a phone.

## After the call: outcome in the app

Once a delivery partner has spoken to the agent, GharSaathi shows the outcome next to
the order: "Zepto rider called · 2:14 pm · Route confirmed — <one-line summary>", and
the order status card says "Rider has your directions" after a confirmed route.

- **Source.** Sarvam's documented, read-only Analytics endpoint
  `GET https://apps.sarvam.ai/api/analytics/v1/{org_id}/{workspace_id}/{app_id}/interactions`
  — <https://docs.sarvam.ai/voice-agents/api/analytics/interactions> (auth, filters, rate
  limits: <https://docs.sarvam.ai/voice-agents/api/analytics/overview>). `app_id` is the
  agent id (`SARVAM_INBOUND_AGENT_ID`, default `GharSaathi--cba37478-61ed`); the same
  `SARVAM_VOICE_AGENT_API_KEY` and org/workspace ids as the cohort stream. The response
  includes the caller's full number (`user_contact`); GharSaathi never reads it.
- **Fields used.** `interaction_id`, `start_datetime`, `end_datetime`, `channel_direction`,
  `deployment_id`, `is_debug_call`, `user_contact_masked`, `user_identifier`, `cohort_id`,
  and `agent_variables.call_outcome` (`route_confirmed`, `owner_transfer_requested`,
  `provider_contact_needed`, `unauthorized_caller`, `general_request_handled`,
  `out_of_scope`, `no_decision`), `delivery_guidance_summary`, `delivery_provider`.
- **When.** The backend polls only while the user has an active order or prepared a
  context in the last 2 h: at most once per 30 s, with backoff on errors.
- **Copy per outcome.** Route confirmed · Caller asked for you · Rider needed help — check
  the Zepto app · Unverified caller — no address shared · Question answered · Not about a
  delivery · No outcome recorded.
- **Privacy.** Stored: time, store, outcome, a ≤140-character summary with codes, long
  numbers and anything address-like removed, and the caller's last four digits. No
  audio, transcript, full number or address. Deletable per call or all at once, and
  removed by "forget everything". The `rider_called` notification (user-toggleable) has
  only the store, the time and the outcome.

Details: `docs/ARCHITECTURE.md` ("Inbound delivery line: call outcomes"); failure rows
FM-IC01–FM-IC04 in `docs/FAILURE_MODES.md`.
