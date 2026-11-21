#!/usr/bin/env bash
# Run the connected-account contract preview only with explicit acknowledgement.
# This wrapper never permits confirmOrder=true. The underlying diagnostic adds
# one inexpensive milk item, calls create_order(confirmOrder=false), and removes
# that item in a finally block.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SEED_PREVIEW=false
ASSUME_YES=false

usage() {
  cat <<'EOF'
Usage: ./scripts/zepto-preview.sh --seed-preview-cleanup [--yes]

This is a live connected-account diagnostic. It may select the first saved Zepto
address and add one inexpensive milk item to the Zepto MCP cart. It only calls
create_order(confirmOrder=false), then removes the seeded item even if preview
fails. It never calls confirmOrder=true and cannot place an order.

--seed-preview-cleanup  Explicitly opt in to the temporary cart mutation.
--yes                   Skip the interactive acknowledgement (for supervised CI).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --seed-preview-cleanup) SEED_PREVIEW=true ;;
    --yes) ASSUME_YES=true ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [[ "${SEED_PREVIEW}" != true ]]; then
  usage >&2
  exit 2
fi

if [[ "${COMMERCE_MODE:-live}" != "live" ]]; then
  printf '%s\n' "Refusing to run: set COMMERCE_MODE=live for a connected-account preview." >&2
  exit 2
fi

if [[ "${ASSUME_YES}" != true ]]; then
  printf '%s' "Type PREVIEW to add then remove one temporary Zepto cart item (no order): "
  read -r acknowledgement
  if [[ "${acknowledgement}" != "PREVIEW" ]]; then
    printf '%s\n' "Cancelled. No provider action was attempted."
    exit 0
  fi
fi

printf '%s\n' "Running an unconfirmed Zepto preview. confirmOrder=true is never invoked."
cd "${REPO_ROOT}/backend"
exec env COMMERCE_MODE=live uv run --with-requirements requirements.txt \
  python scripts/capture_zepto_checkout_contract.py --seed-preview-cleanup
