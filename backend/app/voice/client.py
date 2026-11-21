import hashlib
from typing import Any

import httpx

from app.core.config import Settings
from app.voice.prompts import VOICE_PROMPT_VERSION, VOICE_SYSTEM_PROMPT
from app.voice.schemas import RealtimeClientSecret


class RealtimeSessionError(RuntimeError):
    """A safe, user-facing failure to create a Realtime client secret."""


class RealtimeSessionClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def create_client_secret(self, user_id: str) -> RealtimeClientSecret:
        if not self.settings.openai_api_key.strip():
            raise RealtimeSessionError("Voice is not configured on this server.")

        payload = {
            "expires_after": {"anchor": "created_at", "seconds": 60},
            "session": {
                "type": "realtime",
                "model": self.settings.voice_realtime_model,
                "instructions": VOICE_SYSTEM_PROMPT,
                "max_output_tokens": self.settings.voice_max_output_tokens,
                "audio": {
                    "input": {
                        "noise_reduction": {"type": "near_field"},
                        "turn_detection": {
                            "type": "semantic_vad",
                            "eagerness": "auto",
                            "create_response": True,
                            "interrupt_response": True,
                        },
                    },
                    "output": {
                        "voice": self.settings.voice_name,
                        "speed": self.settings.voice_speed,
                    },
                },
            },
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
            "OpenAI-Safety-Identifier": self._safety_identifier(user_id),
        }

        try:
            async with httpx.AsyncClient(
                base_url=self.settings.openai_api_base_url,
                timeout=15.0,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    "/v1/realtime/client_secrets", headers=headers, json=payload
                )
                response.raise_for_status()
                raw_data: Any = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RealtimeSessionError(
                "Could not start voice right now. Please try again."
            ) from exc

        if not isinstance(raw_data, dict):
            raise RealtimeSessionError(
                "Voice provider returned an invalid session. Please try again."
            )

        data: dict[str, Any] = raw_data
        session = data.get("session")
        if (
            not isinstance(data.get("value"), str)
            or not data["value"]
            or not isinstance(data.get("expires_at"), int)
            or not isinstance(session, dict)
            or not isinstance(session.get("id"), str)
            or not session["id"]
        ):
            raise RealtimeSessionError(
                "Voice provider returned an invalid session. Please try again."
            )

        return RealtimeClientSecret(
            client_secret=data["value"],
            expires_at=data["expires_at"],
            session_id=session["id"],
            model=self.settings.voice_realtime_model,
            voice=self.settings.voice_name,
            prompt_version=VOICE_PROMPT_VERSION,
        )

    @staticmethod
    def _safety_identifier(user_id: str) -> str:
        value = hashlib.sha256(f"equal-ai:{user_id}".encode()).hexdigest()
        return f"user_{value}"
