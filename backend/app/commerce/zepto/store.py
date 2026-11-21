import asyncio
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.commerce.zepto.errors import ZeptoConfigurationError
from app.commerce.zepto.models import StoredConnection


class EncryptedZeptoConnectionStore:
    def __init__(self, path: Path, encryption_key: str) -> None:
        self.path = path
        self._encryption_key = encryption_key.strip()
        self._lock = asyncio.Lock()

    def _fernet(self) -> Fernet:
        if not self._encryption_key:
            raise ZeptoConfigurationError(
                "APP_ENCRYPTION_KEY is required before connecting Zepto."
            )
        try:
            return Fernet(self._encryption_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ZeptoConfigurationError(
                "APP_ENCRYPTION_KEY must be a valid Fernet key."
            ) from exc

    def _read_sync(self) -> StoredConnection | None:
        if not self.path.exists():
            return None
        try:
            payload = self._fernet().decrypt(self.path.read_bytes())
            return StoredConnection.model_validate_json(payload)
        except (InvalidToken, OSError, ValueError) as exc:
            raise ZeptoConfigurationError(
                "The encrypted Zepto connection store cannot be read with the configured key."
            ) from exc

    async def load(self) -> StoredConnection | None:
        async with self._lock:
            return await asyncio.to_thread(self._read_sync)

    def _write_sync(self, connection: StoredConnection) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encrypted = self._fernet().encrypt(connection.model_dump_json().encode("utf-8"))
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_bytes(encrypted)
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)

    async def save(self, connection: StoredConnection) -> None:
        async with self._lock:
            await asyncio.to_thread(self._write_sync, connection)

    async def clear(self) -> None:
        async with self._lock:
            if self.path.exists():
                await asyncio.to_thread(self.path.unlink)
