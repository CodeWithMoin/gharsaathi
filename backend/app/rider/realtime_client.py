"""Server-side WebSocket to OpenAI Realtime for one rider call."""

from __future__ import annotations

import hashlib

from websockets.asyncio.client import ClientConnection, connect

from app.core.config import Settings


class WebsocketRealtime:
    def __init__(self, connection: ClientConnection) -> None:
        self.connection = connection

    async def send(self, message: str) -> None:
        await self.connection.send(message)

    async def recv(self) -> str | bytes:
        return await self.connection.recv()

    async def close(self) -> None:
        await self.connection.close()


def realtime_url(settings: Settings) -> str:
    base = settings.openai_base_url.rstrip("/").replace("https://", "wss://", 1)
    return f"{base}/realtime?model={settings.rider_assist_realtime_model}"


def safety_identifier(user_id: str) -> str:
    return "user_" + hashlib.sha256(f"equal-ai:{user_id}".encode()).hexdigest()


def realtime_connector(settings: Settings, user_id: str):
    async def open_connection() -> WebsocketRealtime:
        api_key = settings.openai_api_key.get_secret_value().strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set.")
        connection = await connect(
            realtime_url(settings),
            additional_headers={
                "Authorization": f"Bearer {api_key}",
                "OpenAI-Safety-Identifier": safety_identifier(user_id),
            },
            max_size=16 * 1024 * 1024,
            open_timeout=10,
        )
        return WebsocketRealtime(connection)

    return open_connection
