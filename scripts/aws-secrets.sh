#!/usr/bin/env bash
# Write the GharSaathi app secret (one JSON secret) into AWS Secrets Manager.
# Never prints a value: only key names and whether each is set.
#
#   scripts/aws-secrets.sh            create or update gharsaathi/app
#   scripts/aws-secrets.sh --check    only report which keys the stored secret has
#
# Values come from .env.aws (gitignored by `.env.*`), falling back to .env for
# OPENAI_API_KEY only. Keys already in the stored secret are kept unless the file
# provides a new value. APP_ENCRYPTION_KEY, ORIGIN_VERIFY_SECRET and
# RIDER_ASSIST_STREAM_SECRET are generated
# once if absent and then preserved (changing APP_ENCRYPTION_KEY would make the
# stored provider connections on EFS unreadable).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export AWS_PROFILE="${AWS_PROFILE:-gharsaathi}"
export AWS_REGION="${AWS_REGION:-ap-south-1}"
export GHARSAATHI_SECRET_NAME="${GHARSAATHI_SECRET_NAME:-gharsaathi/app}"
export GHARSAATHI_ENV_AWS="${GHARSAATHI_ENV_AWS:-${REPO_ROOT}/.env.aws}"
export GHARSAATHI_ENV_LOCAL="${REPO_ROOT}/.env"
export GHARSAATHI_MODE="${1:-write}"

exec python3 - <<'PY'
import base64
import json
import os
import secrets
import subprocess
import sys
import tempfile

REQUIRED = ["OPENAI_API_KEY", "APP_ENCRYPTION_KEY", "CLERK_SECRET_KEY", "ORIGIN_VERIFY_SECRET"]
OPTIONAL = [
    "CLERK_JWT_KEY",
    "WEB_PUSH_VAPID_PUBLIC_KEY",
    "WEB_PUSH_VAPID_PRIVATE_KEY",
    "VOBIZ_AUTH_ID",
    "VOBIZ_AUTH_TOKEN",
    "RIDER_ASSIST_STREAM_SECRET",
]
FALLBACK_FROM_LOCAL = {"OPENAI_API_KEY"}
GENERATED = {
    "APP_ENCRYPTION_KEY": lambda: base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    "ORIGIN_VERIFY_SECRET": lambda: secrets.token_urlsafe(48),
    "RIDER_ASSIST_STREAM_SECRET": lambda: secrets.token_urlsafe(32),
}

name = os.environ["GHARSAATHI_SECRET_NAME"]
mode = os.environ["GHARSAATHI_MODE"]


def read_env(path):
    values = {}
    if not os.path.isfile(path):
        return values
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().removeprefix("export ").strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            values[key] = value
    return values


def aws(*args, capture=True):
    return subprocess.run(["aws", *args, "--output", "json"], check=False, capture_output=capture, text=True)


def stored_secret():
    result = aws("secretsmanager", "get-secret-value", "--secret-id", name)
    if result.returncode != 0:
        if "ResourceNotFoundException" in result.stderr:
            return None, {}
        sys.exit("Could not read the existing secret (check AWS credentials and permissions).")
    payload = json.loads(result.stdout)
    return payload["ARN"], json.loads(payload.get("SecretString") or "{}")


def report(values):
    for key in REQUIRED + OPTIONAL:
        state = "set" if values.get(key) else ("MISSING" if key in REQUIRED else "empty")
        print(f"  {key}: {state}")


arn, current = stored_secret()
if mode == "--check":
    if arn is None:
        sys.exit(f"{name} does not exist yet. Run scripts/aws-secrets.sh.")
    print(f"{name}")
    report(current)
    sys.exit(0 if all(current.get(k) for k in REQUIRED) else 1)
if mode != "write":
    sys.exit("Usage: scripts/aws-secrets.sh [--check]")

aws_file = read_env(os.environ["GHARSAATHI_ENV_AWS"])
local_file = read_env(os.environ["GHARSAATHI_ENV_LOCAL"])
merged = {key: current.get(key, "") for key in REQUIRED + OPTIONAL}
for key in REQUIRED + OPTIONAL:
    if aws_file.get(key):
        if key == "APP_ENCRYPTION_KEY" and merged[key] and merged[key] != aws_file[key]:
            sys.exit("Refusing to replace APP_ENCRYPTION_KEY: stored provider connections would become unreadable.")
        merged[key] = aws_file[key]
    elif not merged[key] and key in FALLBACK_FROM_LOCAL and local_file.get(key):
        merged[key] = local_file[key]
    elif not merged[key] and key in GENERATED:
        merged[key] = GENERATED[key]()

clerk = merged.get("CLERK_SECRET_KEY", "")
if clerk and not clerk.startswith(("sk_live_", "sk_test_")):
    sys.exit("CLERK_SECRET_KEY does not look like a Clerk secret key.")
missing = [key for key in REQUIRED if not merged[key]]
if missing:
    sys.exit(f"Missing required keys (add them to {os.environ['GHARSAATHI_ENV_AWS']}): {', '.join(missing)}")

fd, path = tempfile.mkstemp(prefix="gharsaathi-secret-", suffix=".json")
try:
    os.chmod(path, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(merged, handle)
    if arn is None:
        result = aws("secretsmanager", "create-secret", "--name", name,
                     "--description", "GharSaathi application secrets (scripts/aws-secrets.sh)",
                     "--tags", "Key=Project,Value=GharSaathi",
                     "--secret-string", f"file://{path}")
    else:
        result = aws("secretsmanager", "put-secret-value", "--secret-id", name,
                     "--secret-string", f"file://{path}")
finally:
    os.remove(path)
if result.returncode != 0:
    sys.exit("Secrets Manager rejected the update (details withheld to avoid echoing input).")
arn = json.loads(result.stdout)["ARN"]
print(f"{name} updated: {arn}")
report(merged)
PY
