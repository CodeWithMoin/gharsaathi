# Dev tools (local operator toolkit)

Tools for us, on this Mac only. Nothing here runs in the AWS build, sends data
anywhere, or places, changes or pays for an order.

**Never rate-limit the stores.** `make doctor` makes **zero** Swiggy/Zepto calls:
store readiness comes from our own stored state (token expiry, recorded
rate-limit cooldown, address snapshot age). The one `gs` command that can reach a
store (`gs orders reconcile --dry-run`, and the inspector's Reconcile button)
says so, asks first (`--yes` skips), is refused outright while a cooldown is
recorded, is read-only (`list_order_history`, `get_order_detail`) and runs
behind `ProviderCallBudget` (`backend/app/devtools/budget.py`): at most 5 calls per
command, at least 1 s apart. It makes no call when no order outcome is unknown.

| Command | What it does | Store calls |
|---|---|---|
| `make doctor` | One green/amber/red table; each red line prints its fix; exit 1 on red | 0 (OpenAI `GET /models` once, Jev ping once) |
| `make doctor-offline` / `scripts/gs doctor --offline` | Same, no network beyond this Mac | 0 |
| `make demo-check` | Pre-demo checklist (below); offline unless `ARGS=--online` | 0 |
| `scripts/gs memory list [--all]` · `show <id>` · `delete <id>` · `export [--out f]` | Household memory; delete is the same soft delete + audit event as Settings | 0 |
| `scripts/gs memory add` · `edit` · `import` | TODO hook: lands with `codex/memory-setup` (writer + `demo_seed`). Exit 3 with a pointer meanwhile | – |
| `scripts/gs basket show [--provider P]` · `clear [--provider P]` | Open baskets (amounts via the app formatter); clear empties lines, never a locked (`checkout_pending`) basket | 0 |
| `scripts/gs orders recent` · `status <code>` | Checkout attempts and the tracking watch | 0 |
| `scripts/gs orders reconcile --dry-run` | Read-only check of unknown outcomes; `--apply` stays `make reconcile-unknown ARGS=--apply` | ≤5 Zepto reads, asks first |
| `scripts/gs convo list` · `show <id>` · `last` | Transcript with result/basket/order cards after their message; amounts as stored | 0 |
| `scripts/gs trace last` · `list` · `show <turn>` | Per-turn trace (needs `DEV_TRACE=true`) | 0 |
| `scripts/gs eval list` · `run <name>` · `all [--quick]` · `diff` | Eval scripts, one summary table, `.run/evals/history.jsonl` | 0 (OpenAI/Jev evals call those) |

`scripts/gs` runs `python -m app.devtools` through `scripts/backend-cli.sh`, so it
uses the same database as the app. Every command takes `--user-id` (default: the
only signed-in user) and `--json`. Colors only on a terminal (`--no-color`,
`NO_COLOR`). `make gs ARGS='trace last'` works too.

## Doctor checks

Backend :8000 · Frontend :3003 · Database reachable + alembic at head · Zepto
login (connected, token expiry, recorded cooldown) · Zepto address (selected,
snapshot age) · Swiggy (connected, cooldown) · OpenAI key (one `GET /models`) ·
Jev (configured/enabled, one latency ping) · Web Push VAPID pair · Clerk keys ·
Rider assist config · Zepto port-80 bridge · data dirs writable · running checkout
(branch/commit the app on :8000 was started from; amber when not `main`).

Doctor reads the stores, key file, `frontend/.env.local` and
`.run/launch.json` (written by `scripts/dev.sh` at start) of the checkout the
running app was started from, so running it from a worktree still checks the
real app. It never prints a value from `.env`: only set / valid / reachable.
`COMMERCE_MODE=demo` turns the store lines amber ("not used").

## Turn traces (`DEV_TRACE=true`)

Off by default. When on, typed chat (`app/chat/service.py`) and every voice
delegation (`app/voice/sideband.py`) write one JSONL record per turn to
`.run/traces/turns.jsonl` (gitignored, mode 0600, rotated to the newest
`DEV_TRACE_KEEP`, default 500):

- turn id, time, channel, conversation id (typed) / short session id (voice), prompt version, model, outcome, total ms;
- per model round: model ms, tokens (input, cached, output, reasoning) when reported;
- per tool call: name, argument keys with short values (≤40 chars), result status, sizes of result lists, ms, and the `app/voice/timing.py` stages (e.g. per-store search ms).

Never written: message text, replies, full tool outputs, provider payloads,
images, tokens or secrets. Contact numbers, OTP/PIN/UPI/card fields, tokens,
address parts and every free-text value of `open_address_form` are replaced by
`‹redacted›`; phone-like strings are redacted wherever they appear.

## `/dev` inspector

`http://localhost:3003/dev`, shown only when `NODE_ENV` is not `production`
(so never in a built image) **and** the backend answers `/api/dev/status`. It
lists the last 20 traced turns (expand for rounds, tools, timings, tokens),
open baskets, memory, tracking watches and the notification log, with buttons:
Clear basket, Re-run doctor, Reconcile dry-run (asks first; calls Zepto).

`/api/dev/*` answers 404 unless **all** hold: `DEV_TOOLS_ENABLED=true` (default
false; set to `"false"` in `infra/aws/application.yaml`), no `ALLOWED_USER_IDS`
(a private/hosted deployment never serves it), the request comes from loopback
without proxy headers, and it carries a verified signed-in user. Identity
failures also look like 404. `tests/module/test_private_deployment.py` and
`tests/module/test_dev_api.py` pin this.

## Workflows

**Debug a slow turn.** `DEV_TRACE=true` in `.env`, `make restart`, repeat the
turn, then `scripts/gs trace last`. Model rounds dominate (see "Turn latency" in
`ARCHITECTURE.md`): count rounds, compare `model` vs `tools` totals, look at the
tool's stages (a store at ~4 s is the `PROVIDER_SEARCH_TIMEOUT_SECONDS` cut-off).
`scripts/gs eval run turn-latency` re-benchmarks offline.

**Debug a wrong product.** `scripts/gs trace last` shows the exact `query` the
model searched and the option count; `scripts/gs convo last` shows which result
group and basket card followed. Check remembered/avoided rules with
`scripts/gs memory list`. Offline regressions: `scripts/gs eval run relevance`.

**Seed demo memory.** Today: `make seed-sample-household` (fictional data,
demo mode) and `make unseed-sample-household`. `gs memory add/import` arrive with
`codex/memory-setup`. Verify with `scripts/gs memory list`; back up first with
`scripts/gs memory export --out ~/gs-memory.json`.

**Check before a demo.** `make demo-check`.

## Pre-demo checklist: `make demo-check`

1. Doctor, offline (0 network calls beyond this Mac; `ARGS=--online` adds OpenAI/Jev once each). Any red fails.
2. Quick evals (`gs eval all --quick`: offline evals only), appended to history.
3. Household: at least one confirmed memory, a Zepto address selected, no locked basket.

Prints `READY for the demo` or the list of what to fix; exit 1 when not ready.
`scripts/gs eval diff` afterwards shows whether anything got worse since the last run.
