import os
from pathlib import Path

from cryptography.fernet import Fernet


def get_or_create_local_encryption_key(path: Path) -> str:
    """Return a stable, private key for encrypted local provider sessions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        key = Fernet.generate_key()
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return path.read_text(encoding="ascii").strip()
        with os.fdopen(descriptor, "wb") as key_file:
            key_file.write(key)
        return key.decode("ascii")
