# Inbound delivery-assist line

The Sarvam number `+918064264240` routes inbound calls to a committed agent
version. The agent behaves as Moin's household shopping/delivery assistant.
“Sir, Zepto”, “Sir, Swiggy”, and equivalent delivery introductions start the
delivery flow. Other callers are offered a transfer to Moin.

## What the line is told, and where it comes from

“The address we select is what comes up there.” Nothing is retyped:

| Variable | Source (server-side only) |
|---|---|
| `caller_authorized` | `yes` after the owner approves; `no` when cleared |
| `recipient_name` | `first_name` from the user's profile (`/api/profile`); editable in the panel, saved to the profile |
| `delivery_address` | Full `address_line` of the store address the user **selected** (the saved Swiggy/Zepto snapshot; the demo fixture in demo mode) |
| `landmark` | Saved directions for that address (one line). With the route flag off, a gate note is appended as `… Gate: …` |
| `route_directions` | Saved steps (up to 8 short ones) plus the gate note, as `1) …; 2) …`. **Only with `SARVAM_ROUTE_DIRECTIONS_ENABLED=true`** |
| `handover_instructions` | One short line (quick picks: “Guard ke paas chhod dijiye”, “Door ke bahar rakh dijiye”, “Neeche aake call kijiye — main aa jaunga”, “Mujhe hi dijiye”, or free text). **Only with the same flag** |

Agent v5 declares only the first four variables, so the flag defaults to
`false` and no undeclared variable is ever sent. Turn it on once the deployed
agent declares `route_directions` and `handover_instructions` (default value
`NOT_AVAILABLE`); an unsaved step list or handover note is then sent as
`NOT_AVAILABLE`. Values are always strings; any other variable name is refused in
code (`app/inbound_delivery/sarvam.py`). `caller_authorized=yes` is never sent
with an empty address.

Directions are saved per user and per store address (`provider` + `address_id`,
table `delivery_directions`), edited in a popup and reused automatically next
time. They can be deleted. They may not contain OTPs, PINs, passwords, payment
details or links.

## Sharing

The browser sends only `{provider, address_id, confirmed: true}` to
`POST /api/inbound-delivery/contexts`. `confirmed` is a strict boolean (`1`,
`"true"` are refused). The server checks the id is the currently selected
address (409 otherwise), reads the name and directions, and streams one record
for the server-configured caller (`SARVAM_DEMO_RECIPIENT_NUMBER`) to Sarvam's
inbound-deployment cohort stream endpoint. The browser never supplies a phone
number or address text, never sees the Voice Agents API key, and nothing places
a call.

The line is **ready** only when the backend sees the cohort `completed` with
exactly one valid record and zero rejected. `processing` is polled via
`GET /api/inbound-delivery/contexts/{cohort_id}` (scoped to the user who
streamed it; the in-memory cohort-owner map is capped at 128 entries, and the
user's latest cohort id is also stored).

**Automatic sharing** (“Share automatically when an order is on its way”) is off
by default. Turning it on needs its own explicit approval
(`PUT /api/inbound-delivery/line/settings {auto_share: true, confirmed: true}`).
When on, starting tracking for an order shares the selected address for that
store and sends a `delivery_line` notification (ready / not shared / not
confirmed). No address appears in notifications.

## Clearing (revoke)

The line is cleared by streaming a replacement record for the same number with
`caller_authorized=no` and empty `recipient_name`, `delivery_address`,
`landmark` (and, with the flag on, empty `route_directions` and
`handover_instructions`). It happens:

- when the selected address for the shared store changes (address picker,
  voice, another tab);
- when the tracked order is delivered or cancelled (for an automatic share, only
  that order);
- when the user taps **Clear delivery line**.

The panel shows “Delivery line cleared” with the reason. A failed clear is shown
as a problem and blocks a new share until cleared.

**Uncertainty.** Sarvam's docs say stream cohort is for “add or refresh contacts
over time”, but do not state which record wins when one number appears in
several cohorts, and there is no documented endpoint to delete one record from an
inbound deployment. GharSaathi assumes the newest record wins and still sends the
clear. Verify with a test call before relying on it.

## Errors

| Sarvam answer | API | Meaning |
|---|---|---|
| 401 / 403 | 503 `sarvam_not_authorised` | Server key problem |
| other 4xx | 502 `sarvam_rejected` | Request refused |
| 429 / 5xx / connection refused | 503 `sarvam_unavailable` | Nothing changed; try later |
| timeout after sending | 504 `outcome_unknown` | Not retried; saved as `unknown` so it is cleared later |
| unreadable body | 502 `sarvam_unreadable` | |

Logs carry the operation and status code only — never numbers, addresses,
directions, provider text or the key. Numbers shown in the UI are masked to the
last four digits.

## Who may use it

Only the owner: `SARVAM_INBOUND_OWNER_USER_ID` if set, otherwise
`ALLOWED_USER_IDS`. With neither set the feature is off (config reason
`owner_not_configured`). Sharing and the auto-share switch also need current
legal consent. Every route is behind the private-deployment check.

Unknown callers can reach the agent but receive no address. Address disclosure
requires both an approved caller match and an explicit delivery confirmation.
The agent never requests an OTP, payment or tip and never exposes Moin's phone
number; transfers happen through Sarvam only after caller confirmation.

Automated tests mock every Sarvam response. They never dial a phone.
