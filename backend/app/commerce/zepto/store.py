import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.commerce.zepto.errors import ZeptoConfigurationError
from app.commerce.zepto.models import PendingAuthorization, StoredConnection


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
            current = await asyncio.to_thread(self._read_sync)
            if current is not None and not connection.pending_authorizations:
                connection = connection.model_copy(
                    update={
                        "pending_authorizations": current.pending_authorizations,
                    }
                )
            await asyncio.to_thread(self._write_sync, connection)

    async def save_pending(
        self,
        state: str,
        pending: PendingAuthorization,
    ) -> None:
        async with self._lock:
            connection = await asyncio.to_thread(self._read_sync)
            if connection is None:
                connection = StoredConnection(client_id=pending.client_id)
            now = datetime.now(UTC)
            connection.pending_authorizations = {
                key: value
                for key, value in connection.pending_authorizations.items()
                if now - value.created_at < timedelta(minutes=10)
            }
            connection.pending_authorizations[state] = pending
            await asyncio.to_thread(self._write_sync, connection)

    async def pop_pending(self, state: str) -> PendingAuthorization | None:
        async with self._lock:
            connection = await asyncio.to_thread(self._read_sync)
            if connection is None:
                return None
            pending = connection.pending_authorizations.pop(state, None)
            await asyncio.to_thread(self._write_sync, connection)
            return pending

    async def clear(self) -> None:
        async with self._lock:
            if self.path.exists():
                await asyncio.to_thread(self.path.unlink)
