#!/usr/bin/env bash
# Build the hackathon submission zip from the committed tree plus the docs/deck/video
# files we add. Secrets and local state are never included: the archive starts from
# `git archive` (tracked files only), and every candidate is scanned before zipping.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${OUT_DIR:-${REPO_ROOT}/../submission}"
STAMP="$(date +%H%M)"
WORK="$(mktemp -d)"
NAME="gharsaathi"
trap 'rm -rf "${WORK}"' EXIT

cd "${REPO_ROOT}"
mkdir -p "${WORK}/${NAME}" "${OUT_DIR}"
git archive --format=tar HEAD | tar -x -C "${WORK}/${NAME}"

# Uncommitted reviewer docs (AGENTS.md edits, VIDEO.md, video script) — copy the working copy.
for f in AGENTS.md VIDEO.md docs/VIDEO_SCRIPT.md; do
  [[ -f "${f}" ]] && mkdir -p "${WORK}/${NAME}/$(dirname "${f}")" && cp "${f}" "${WORK}/${NAME}/${f}"
done
# Deck (built outside the repo).
DECK_DIR="${DECK_DIR:-}"
if [[ -n "${DECK_DIR}" && -d "${DECK_DIR}" ]]; then
  mkdir -p "${WORK}/${NAME}/deck" && cp -R "${DECK_DIR}/." "${WORK}/${NAME}/deck/"
fi

# Owner decision: ship the Clerk DEVELOPMENT (test-mode) keys inside the zip only, so reviewers
# can sign in with the +clerk_test login. They are never committed. Live keys are refused.
ENV_LOCAL="${REPO_ROOT}/frontend/.env.local"
PK="$(grep -E '^NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=' "${ENV_LOCAL}" | cut -d= -f2- || true)"
SK="$(grep -E '^CLERK_SECRET_KEY=' "${ENV_LOCAL}" | cut -d= -f2- || true)"
if [[ "${PK}" == pk_test_* && "${SK}" == sk_test_* ]]; then
  python3 - "${WORK}/${NAME}/AGENTS.md" "${PK}" "${SK}" <<'PY'
import sys
path, pk, sk = sys.argv[1:4]
text = open(path, encoding="utf-8").read()
text = text.replace("__CLERK_PUBLISHABLE_KEY__", pk).replace("__CLERK_SECRET_KEY__", sk)
open(path, "w", encoding="utf-8").write(text)
PY
  echo "Filled Clerk test keys into the packaged AGENTS.md only."
else
  echo "Clerk keys are not test keys; leaving placeholders." >&2
fi

# Never ship local state or secrets even if something slipped into the tree.
find "${WORK}/${NAME}" \( -name '.env' -o -name '.env.local' -o -name '.env.aws' -o -name '*.enc' \
  -o -name 'node_modules' -o -name '.next' -o -name '.data' -o -name '.run' -o -name '.live-smoke' \
  -o -name '__pycache__' -o -name '.venv' -o -name '.serena' \) -prune -exec rm -rf {} +

# Secret scan: fail the build on anything that looks like a real key.
if grep -rIlE 'sk-proj-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{32,}|sk_live_[A-Za-z0-9]{10,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----' "${WORK}/${NAME}" >/dev/null; then
  echo "Refusing to package: a secret-looking value was found in:" >&2
  grep -rIlE 'sk-proj-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{32,}|sk_live_[A-Za-z0-9]{10,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----' "${WORK}/${NAME}" >&2
  exit 1
fi

ZIP="${OUT_DIR}/${NAME}-submission-${STAMP}.zip"
(cd "${WORK}" && zip -qr "${ZIP}" "${NAME}")
echo "Wrote ${ZIP} ($(du -h "${ZIP}" | cut -f1))"
