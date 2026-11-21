import stat

from cryptography.fernet import Fernet

from app.core.local_secrets import get_or_create_local_encryption_key


def test_local_encryption_key_is_private_and_stable(tmp_path) -> None:
    path = tmp_path / "provider.key"

    first = get_or_create_local_encryption_key(path)
    second = get_or_create_local_encryption_key(path)

    assert first == second
    Fernet(first.encode("ascii"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
