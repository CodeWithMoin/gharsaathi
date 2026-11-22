"""Throwaway SQLite household memory for delegation scenarios (no PostgreSQL, no network).

A scenario's ``memory_db`` block may seed active preferences (``seed``) and
repeated basket choices (``choices``, turned into a pending suggestion by the
same deterministic check the app runs after a conversation save).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.database.base import Base
from app.database.models import MemoryChoiceSignal, MemoryItem, MemoryStatus
from app.memory.choice_signals import propose_from_choices
from app.memory.household import CONSENT_TYPES, PreferenceSpec
from app.memory.household_service import HouseholdPreferenceService
from app.memory.recall import DatabasePreferenceRecaller
from app.memory.schemas import MemoryPermissionUpsert
from app.memory.service import MemoryService


class ScenarioMemoryDatabase:
    def __init__(self, engine: AsyncEngine, directory: tempfile.TemporaryDirectory[str], household_id: str) -> None:
        self.engine = engine
        self.directory = directory
        self.household_id = household_id
        self.factory = async_sessionmaker(engine, expire_on_commit=False)
        self.recaller = DatabasePreferenceRecaller(self.factory)

    @classmethod
    async def create(cls, household_id: str, config: dict[str, Any]) -> ScenarioMemoryDatabase:
        directory = tempfile.TemporaryDirectory(prefix="gharsaathi_memory_eval_")
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(directory.name) / 'memory.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        database = cls(engine, directory, household_id)
        await database._seed(config)
        return database

    async def _seed(self, config: dict[str, Any]) -> None:
        async with self.factory() as session:
            memory = MemoryService(session)
            actor = await memory.actor_for_external_id(self.household_id)
            for memory_type in CONSENT_TYPES:
                await memory.set_permission(
                    actor, memory_type, "personalize_search",
                    MemoryPermissionUpsert(allowed=config.get("consent", True)),
                )
            service = HouseholdPreferenceService(session)
            for entry in config.get("seed", []):
                await service.save_stated(actor, PreferenceSpec.model_validate(entry))
            for choice in config.get("choices", []):
                for index in range(choice.get("occasions", 3)):
                    session.add(
                        MemoryChoiceSignal(
                            subject_id=actor.id,
                            occasion_key=f"seed-{index}",
                            provider=choice["provider"],
                            item=choice["item"],
                            product_name=choice["product_name"],
                            brand=choice.get("brand"),
                            pack_size=choice.get("pack_size"),
                            competing_providers=[],
                        )
                    )
            await session.commit()
            if config.get("choices"):
                await propose_from_choices(session, actor)

    async def state(self) -> dict[str, list[str]]:
        async with self.factory() as session:
            items = list((await session.execute(select(MemoryItem))).scalars())
        def summaries(status: MemoryStatus) -> list[str]:
            return sorted(item.text for item in items if item.status == status)
        return {
            "active": summaries(MemoryStatus.CONFIRMED),
            "pending": summaries(MemoryStatus.PROPOSED),
        }

    @staticmethod
    def check(expect: dict[str, Any], state: dict[str, list[str]]) -> list[str]:
        failures: list[str] = []
        active = " | ".join(state["active"]).casefold()
        for needle in expect.get("active_memory_contains", []):
            if needle.casefold() not in active:
                failures.append(f"No active memory mentions {needle!r} (active: {state['active']}).")
        for needle in expect.get("active_memory_excludes", []):
            if needle.casefold() in active:
                failures.append(f"An active memory still mentions {needle!r} (active: {state['active']}).")
        if "active_memory_count" in expect and len(state["active"]) != expect["active_memory_count"]:
            failures.append(f"Expected {expect['active_memory_count']} active memories, got {state['active']}.")
        return failures

    async def close(self) -> None:
        await self.engine.dispose()
        self.directory.cleanup()
