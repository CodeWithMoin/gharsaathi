#!/usr/bin/env bash
# Stage the hackathon submission folder from the committed tree plus the deck.
# The caller creates the final zip after inspecting this folder.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${OUT_DIR:-${REPO_ROOT}/../submission}"
WORK="$(mktemp -d)"
NAME="gharsaathi"
trap 'rm -rf "${WORK}"' EXIT

cd "${REPO_ROOT}"
DEST="${OUT_DIR}/${NAME}-final"
if [[ -e "${DEST}" ]]; then
  echo "Refusing to overwrite existing output: ${DEST}" >&2
  echo "Move or remove that generated folder, then run this script again." >&2
  exit 1
fi

mkdir -p "${WORK}/${NAME}" "${OUT_DIR}"
git archive --format=tar HEAD | tar -x -C "${WORK}/${NAME}"

# Deck (built outside the repo).
DECK_DIR="${DECK_DIR:-}"
if [[ -n "${DECK_DIR}" && -d "${DECK_DIR}" ]]; then
  mkdir -p "${WORK}/${NAME}/deck" && cp -R "${DECK_DIR}/." "${WORK}/${NAME}/deck/"
fi

# Never ship local state or secrets even if something slipped into the tree.
find "${WORK}/${NAME}" \( -name '.env' -o -name '.env.local' -o -name '.env.aws' -o -name '*.enc' \
  -o -name 'node_modules' -o -name '.next' -o -name '.data' -o -name '.run' -o -name '.live-smoke' \
  -o -name '__pycache__' -o -name '.venv' -o -name '.serena' \) -prune -exec rm -rf {} +

# Secret scan: fail on common private-key formats, including development secrets.
SECRET_PATTERN='sk-proj-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{32,}|sk_(test|live)_[A-Za-z0-9]{10,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----'
if grep -rIlE "${SECRET_PATTERN}" "${WORK}/${NAME}" >/dev/null; then
  echo "Refusing to package: a secret-looking value was found in:" >&2
  grep -rIlE "${SECRET_PATTERN}" "${WORK}/${NAME}" >&2
  exit 1
fi

for required in AGENTS.md VIDEO.md README.md backend/requirements.txt frontend/package.json; do
  if [[ ! -f "${WORK}/${NAME}/${required}" ]]; then
    echo "Missing required submission file: ${required}" >&2
    exit 1
  fi
done
if [[ ! -f "${WORK}/${NAME}/deck/deck.pdf" ]]; then
  echo "Missing deck/deck.pdf. Set DECK_DIR to the reviewed deck folder." >&2
  exit 1
fi

mv "${WORK}/${NAME}" "${DEST}"
echo "Staged ${DEST} ($(du -sh "${DEST}" | cut -f1))"
