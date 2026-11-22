#!/usr/bin/env bash
# Opt-in live provider smoke suite. Talks to the REAL Swiggy/Zepto MCP servers
# using the locally stored connections. Never confirms, pays for, or places an
# order (see docs/LIVE_SMOKE.md). Run only with the account owner's consent.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CART=false
ASSUME_YES=false

usage() {
  cat <<'EOF'
Usage: ./scripts/live-smoke.sh [--cart] [--yes]

(default)  Tier 1, read-only: connection status, tool catalogue drift, saved
           address shape, one "atta" search per provider, and the shape of
           Zepto's get_payment_methods. Zepto's selected delivery address may
           be set server-side; nothing else changes.
--cart     Tier 2, CART-MUTATING: replaces the Zepto cart with one cheap item,
           requests create_order(confirmOrder=false), parses the COD preview,
           asks the three prepaid order tools for unconfirmed previews (shapes
           only), then removes the item. Never confirms, pays or polls.
--yes      Skip the interactive acknowledgement (supervised use only).

A content-free schema report is written to backend/.live-smoke/latest.json.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cart) CART=true ;;
    --yes) ASSUME_YES=true ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [[ "${CART}" == true ]]; then
  MARK="live_cart"
  WORD="REPLACE"
  cat <<'EOF'
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!!  LIVE CART TIER — THIS REPLACES THE CONNECTED ZEPTO CART.
!!  One cheap item is added, an UNCONFIRMED cash-on-delivery preview is
!!  requested, and the item is removed again. No order is ever placed.
!!  By default it refuses to run unless the Zepto cart is already empty.
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
EOF
else
  MARK="live and not live_cart"
  WORD="CONSENT"
  printf '%s\n' "Live read-only smoke: real Swiggy/Zepto requests on the connected accounts."
fi

if [[ "${ASSUME_YES}" != true ]]; then
  printf 'The account owner has agreed to this run. Type %s to continue: ' "${WORD}"
  read -r acknowledgement
  if [[ "${acknowledgement}" != "${WORD}" ]]; then
    printf '%s\n' "Cancelled. No provider request was made."
    exit 0
  fi
fi

cd "${REPO_ROOT}/backend"
ENV_ARGS=(GHARSAATHI_LIVE_SMOKE=1)
if [[ "${CART}" == true ]]; then
  ENV_ARGS+=(GHARSAATHI_LIVE_SMOKE_CART=1)
else
  ENV_ARGS+=(GHARSAATHI_LIVE_SMOKE_CART=0)
fi
exec env "${ENV_ARGS[@]}" uv run --with-requirements requirements.txt \
  pytest tests/live --run-live -m "${MARK}" -s -rs -W default -p no:cacheprovider
