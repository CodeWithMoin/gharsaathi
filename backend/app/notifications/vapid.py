"""Generate a VAPID key pair for browser notifications (``make vapid-keys``).

Prints the keys to this terminal only (stdout); nothing is written to disk or
logged. Paste them into the repository's ``.env``.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate() -> tuple[str, str]:
    """``(public_key, private_key)``: the browser's applicationServerKey and the raw private key."""

    private = ec.generate_private_key(ec.SECP256R1())
    raw_private = private.private_numbers().private_value.to_bytes(32, "big")
    public = private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return _b64url(public), _b64url(raw_private)


def main() -> None:
    public, private = generate()
    print("New VAPID keys for GharSaathi browser notifications.")
    print("Add these three lines to .env (keep the private key secret; never commit it):\n")
    print(f"WEB_PUSH_VAPID_PUBLIC_KEY={public}")
    print(f"WEB_PUSH_VAPID_PRIVATE_KEY={private}")
    print("WEB_PUSH_SUBJECT=mailto:you@example.com\n")
    print("Then restart the backend. Changing the keys later signs every browser out of")
    print("notifications: each person turns them on again in Settings -> Notifications.")


def public_key_for(private_key: str) -> str | None:
    """The public key that belongs to a raw base64url private key, or None if invalid."""

    try:
        padded = private_key.strip() + "=" * (-len(private_key.strip()) % 4)
        raw = base64.urlsafe_b64decode(padded)
        if len(raw) != 32:
            return None
        private = ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())
    except (ValueError, TypeError):
        return None
    return _b64url(
        private.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )


if __name__ == "__main__":
    main()
