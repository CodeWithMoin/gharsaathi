"""Offline benchmark of voice session start on the backend.

Every dependency is a fake with a fixed delay (no database, no OpenAI, no
providers), so the numbers show how the stages combine, not real latency.

    cd backend && PYTHONPATH=. uv run --with-requirements requirements.txt \
        python scripts/bench_voice_start.py
"""

import asyncio
import logging
import statistics
import time

import httpx
from pydantic import SecretStr

from app.core.config import Settings
from app.voice.service import LiveSessionService

OFFER = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
RUNS = 5

SCENARIOS = {
    # Delays in seconds: profile read, memory summary, chat context, OpenAI.
    "typical": (0.03, 0.05, 0.12, 0.6),
    "slow context": (0.03, 0.05, 2.5, 0.6),
    "slow database": (0.4, 0.4, 0.4, 0.6),
}


class NoopHub:
    def start(self, *args: object, **kwargs: object) -> None:
        return None


class NoSearch:
    async def search(self, *args: object) -> object:
        raise AssertionError("search must not run here")


class Delayed:
    def __init__(self, delay: float) -> None:
        self.delay = delay

    async def _wait(self) -> None:
        await asyncio.sleep(self.delay)


class Profiles(Delayed):
    async def read(self, household_id: str) -> None:
        await self._wait()


class Memory(Delayed):
    async def household_memory(self, household_id: str) -> None:
        await self._wait()


class Context(Delayed):
    async def load(self, household_id: str) -> None:
        await self._wait()


async def run(scenario: tuple[float, float, float, float]) -> tuple[float, dict[str, float]]:
    profile, memory, context, openai = scenario

    async def answer(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(openai)
        return httpx.Response(
            201, json={"session": {"id": "live_bench"}, "transport": {"sdp": "answer"}}
        )

    settings = Settings(_env_file=None, openai_api_key=SecretStr("sk-bench"), voice_event_log=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        service = LiveSessionService(
            settings,
            NoSearch(),  # type: ignore[arg-type]
            NoopHub(),  # type: ignore[arg-type]
            client,
            preference_recall=Memory(memory),  # type: ignore[arg-type]
            profile_reader=Profiles(profile),  # type: ignore[arg-type]
            context_reader=Context(context),  # type: ignore[arg-type]
        )
        started = time.perf_counter()
        session = await service.create(OFFER, household_id="bench-user")
        stages = getattr(session, "start_timing_ms", None) or {}
        return (time.perf_counter() - started) * 1000, stages


async def main() -> None:
    logging.disable(logging.WARNING)
    for name, scenario in SCENARIOS.items():
        runs = [await run(scenario) for _ in range(RUNS)]
        total = statistics.median(total for total, _ in runs)
        names = runs[0][1].keys()
        stages = "  ".join(
            f"{stage}={statistics.median(r[1][stage] for r in runs):.0f}" for stage in names
        )
        print(f"{name:14} total {total:7.1f} ms  {stages}  (fake delays {scenario})")


if __name__ == "__main__":
    asyncio.run(main())
