"""Manual seed of the owner's own household memory, from a file they answered.

The household's answers (diet, keep-outs, usual buys, nevers, style, store,
delivery notes, language) are written for one signed-in user through the same
structured memory writer the app uses, as things the owner told us (source
"told"). Nothing here is inferred.

Usage (from the repo root; the database is the local app's, see scripts/backend-cli.sh):

    make memory-seed ARGS="--file my-home.yaml --dry-run"
    make memory-seed ARGS="--file my-home.yaml --apply [--user-id USER] [--turn-on-memory]"
    make memory-seed ARGS="--remove-seeded [--user-id USER] --apply"

- ``--dry-run`` (the default) writes nothing and prints the plan.
- ``--apply`` is idempotent: unchanged items stay, changed ones are replaced
  (the old one is kept as history), seeded items no longer in the file are
  removed. Items are tagged with the ``demo-memory-seed`` conversation marker.
- ``--remove-seeded`` deletes exactly the tagged items (restoring any earlier
  memory of yours that one of them had replaced). Language and address form
  live in the profile and are left as they are.
- Preference memory must be on for the user (Settings → Saved preferences), or
  pass ``--turn-on-memory`` to turn it on as part of the owner's own request.

The plan lists the owner's own items in this terminal only; user ids are masked.
See docs/demo-memory.example.yaml for the file format.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    MemoryEventType,
    MemoryItem,
    MemorySourceType,
    MemoryStatus,
    MemorySubject,
    SubjectType,
    UserProfileRecord,
)
from app.memory.household import (
    HOUSEHOLD_FACTS,
    MAX_USUAL_QUANTITY,
    VALUE_KEY_BY_KIND,
    PreferenceSpec,
    kind_of,
    sensitive_category,
    text_for_value,
)
from app.memory.household_service import HouseholdPreferenceService, MemoryPolicyError
from app.memory.repository import MemoryRepository
from app.memory.service import MemoryService
from app.rider.safety import mentions_secret
from app.user_profile import UserProfileUpdate, get_profile_record, update_profile

SEED_MARKER = "demo-memory-seed"
# Kinds that hold one value per item: a new value replaces the old one.
SINGLE_KINDS = frozenset(
    {"diet", "shopping_style", "household", "prefer_pack_size", "usual_quantity", "prefer_store"}
)
# Free words people use for a one-person home.
_PEOPLE_WORDS = {
    "just me": 1, "me": 1, "only me": 1, "alone": 1, "single": 1, "one": 1, "two": 2, "three": 3,
    "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_DIET_ALIASES = {
    "veg": "vegetarian", "vegetarian": "vegetarian", "pure veg": "vegetarian",
    "egg": "eggetarian", "eggetarian": "eggetarian", "veg + egg": "eggetarian", "veg with egg": "eggetarian",
    "jain": "jain",
    "non veg": "non_vegetarian", "nonveg": "non_vegetarian", "non vegetarian": "non_vegetarian",
    "non_vegetarian": "non_vegetarian", "non-vegetarian": "non_vegetarian",
}
_ORDER_TIMES = ("early morning", "morning", "afternoon", "evening", "late night", "night")
_IGNORED_KEYS = {"origin", "user_words"}


class SeedError(Exception):
    """A clear, content-free reason the seed cannot run."""


# File schema -----------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _yes_no(value: Any) -> Any:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return value


class HouseholdAnswers(_Strict):
    people: int | None = Field(default=None, ge=1, le=15)
    kids: Literal["yes", "no"] | None = None
    baby: Literal["yes", "no"] | None = None
    pets: Literal["none", "dog", "cat", "dog_and_cat", "other"] | None = None

    @field_validator("kids", "baby", mode="before")
    @classmethod
    def yes_no(cls, value: Any) -> Any:
        return _yes_no(value)

    @field_validator("people", mode="before")
    @classmethod
    def people_count(cls, value: Any) -> Any:
        if isinstance(value, str):
            words = " ".join(value.casefold().replace("-", " ").split())
            if words in _PEOPLE_WORDS:
                return _PEOPLE_WORDS[words]
            digits = "".join(char for char in words if char.isdigit())
            return int(digits) if digits else value
        return value

    @field_validator("pets", mode="before")
    @classmethod
    def pets_value(cls, value: Any) -> Any:
        if value is False or value in ([], None):
            return None if value is None else "none"
        if isinstance(value, list):
            names = {str(item).strip().casefold() for item in value}
            if names == {"dog"} or names == {"cat"}:
                return names.pop()
            if names == {"dog", "cat"}:
                return "dog_and_cat"
            return "other"
        return str(value).strip().casefold().replace(" ", "_")


class UsualBuy(_Strict):
    category: str = Field(min_length=2, max_length=60)
    brand: str | None = Field(default=None, max_length=80)
    product: str | None = Field(default=None, max_length=80)
    size: str | None = Field(default=None, max_length=80)
    quantity: int | None = Field(default=None, ge=1, le=MAX_USUAL_QUANTITY)


class AvoidAnswers(_Strict):
    products: list[str] = Field(default_factory=list, max_length=30)
    brands: list[str] = Field(default_factory=list, max_length=30)
    stores: list[str] = Field(default_factory=list, max_length=2)


class DemoMemoryFile(_Strict):
    household: HouseholdAnswers | None = None
    diet: Literal["vegetarian", "eggetarian", "jain", "non_vegetarian"] | None = None
    allergies: list[str] = Field(default_factory=list, max_length=20)
    usual_buys: list[UsualBuy] = Field(default_factory=list, max_length=60)
    avoid: AvoidAnswers = Field(default_factory=AvoidAnswers)
    value_preference: Literal["cheapest", "balanced", "premium"] | None = None
    pack_size_rule: Literal["prefer_larger_packs", "prefer_smaller_packs", "prefer_best_unit_value"] | None = None
    substitution_rule: Literal["ask_before_substitute", "substitute_closest_and_tell"] | None = None
    usual_order_times: list[str] = Field(default_factory=list, max_length=6)
    preferred_store: str | None = None
    delivery_notes: list[str] = Field(default_factory=list, max_length=5)
    goals: list[str] = Field(default_factory=list, max_length=5)
    routines: list[str] = Field(default_factory=list, max_length=10)
    language: Literal["hinglish", "hindi", "english"] | None = None
    address_form: Literal["masculine", "feminine", "neutral"] | None = None

    @field_validator("diet", mode="before")
    @classmethod
    def diet_value(cls, value: Any) -> Any:
        if not value:
            return None
        words = " ".join(str(value).casefold().replace("-", " ").split())
        return _DIET_ALIASES.get(words, words.replace(" ", "_"))

    @field_validator("pack_size_rule", mode="before")
    @classmethod
    def pack_rule(cls, value: Any) -> Any:
        words = " ".join(str(value or "").casefold().replace("_", " ").split())
        if not words:
            return None
        if "unit" in words or "value" in words:
            return "prefer_best_unit_value"
        if any(word in words for word in ("large", "big", "bulk")):
            return "prefer_larger_packs"
        if "small" in words:
            return "prefer_smaller_packs"
        return value

    @field_validator("substitution_rule", mode="before")
    @classmethod
    def substitution(cls, value: Any) -> Any:
        words = " ".join(str(value or "").casefold().replace("_", " ").split())
        if not words:
            return None
        if any(word in words for word in ("closest", "similar", "alternative")):
            return "substitute_closest_and_tell"
        if "ask" in words:
            return "ask_before_substitute"
        return value

    @field_validator("usual_order_times", mode="before")
    @classmethod
    def order_times(cls, value: Any) -> Any:
        items = [value] if isinstance(value, str) else (value or [])
        cleaned = [" ".join(str(item).casefold().replace("-", " ").split()) for item in items]
        unknown = [item for item in cleaned if item not in _ORDER_TIMES]
        if unknown:
            raise ValueError(f"use {', '.join(_ORDER_TIMES)}")
        return cleaned

    @field_validator("avoid", mode="before")
    @classmethod
    def avoid_shape(cls, value: Any) -> Any:
        # A list means products to never suggest: "chocolate" or {item: ..., reason: ...}.
        # A reason is not stored: only what to avoid is needed for shopping.
        if isinstance(value, list):
            products = []
            for entry in value:
                if isinstance(entry, dict):
                    unknown = set(entry) - {"item", "reason"}
                    if unknown or "item" not in entry:
                        raise ValueError("each avoid entry is a name or {item, reason}")
                    products.append(entry["item"])
                else:
                    products.append(entry)
            return {"products": products}
        return value or {}

    @field_validator("allergies", "delivery_notes", "goals", "routines", mode="before")
    @classmethod
    def as_list(cls, value: Any) -> Any:
        if value is None:
            return []
        return [value] if isinstance(value, str) else value


def load_file(path: Path) -> DemoMemoryFile:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SeedError(f"Cannot read {path}: {exc.strerror or exc}.") from exc
    try:
        if path.suffix.casefold() == ".json":
            raw = json.loads(text)
        else:
            import yaml

            raw = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001 - any parse error is reported plainly
        raise SeedError(f"{path.name} is not valid {'JSON' if path.suffix == '.json' else 'YAML'}: {exc}") from exc
    return parse_answers(raw)


def parse_answers(raw: Any) -> DemoMemoryFile:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise SeedError("The file must be a mapping of topics (household, diet, usual_buys, ...).")
    try:
        return DemoMemoryFile.model_validate(raw)
    except ValidationError as exc:
        raise SeedError("The file has problems:\n" + _format_errors(exc)) from exc


def _format_errors(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors():
        where = ".".join(str(part) for part in error["loc"]) or "file"
        message = error["msg"]
        if error["type"] == "extra_forbidden":
            message = "unknown field (check the spelling against docs/demo-memory.example.yaml)"
        lines.append(f"  - {where}: {message}")
    return "\n".join(lines)


# Plan ------------------------------------------------------------------------


@dataclass(frozen=True)
class Wanted:
    spec: PreferenceSpec
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def value(self) -> dict[str, Any]:
        return {**self.spec.to_value("explicit"), **self.extra}

    @property
    def text(self) -> str:
        return text_for_value(self.spec.kind, self.value)

    def key(self) -> tuple[str, str]:
        if self.spec.kind in SINGLE_KINDS:
            return self.spec.kind, (self.spec.item or "").casefold()
        if self.spec.kind == "rule":
            # One pack-size rule and one substitution rule, each replaceable.
            return self.spec.kind, _rule_family(self.spec.value)
        return self.spec.kind, f"{(self.spec.item or '').casefold()}|{self.spec.value.casefold()}"


def _rule_family(rule: str) -> str:
    if rule.endswith("_packs") or rule == "prefer_best_unit_value":
        return "pack_size"
    if "substitute" in rule:
        return "substitution"
    return rule


def _spec(where: str, **fields: Any) -> PreferenceSpec:
    try:
        return PreferenceSpec(**fields)
    except ValidationError as exc:
        reason = exc.errors()[0]["msg"].removeprefix("Value error, ")
        raise SeedError(f"{where}: {reason}") from exc


def wanted_items(answers: DemoMemoryFile) -> list[Wanted]:
    """Every memory the file asks for, validated and policy-checked; raises SeedError."""
    wanted: list[Wanted] = []
    if answers.household is not None:
        for fact in HOUSEHOLD_FACTS:
            value = getattr(answers.household, fact)
            if value is not None:
                wanted.append(Wanted(_spec(f"household.{fact}", kind="household", item=fact, value=str(value))))
    if answers.diet:
        wanted.append(Wanted(_spec("diet", kind="diet", value=answers.diet)))
    for index, allergen in enumerate(answers.allergies):
        wanted.append(Wanted(_spec(f"allergies.{index}", kind="avoid_ingredient", value=allergen)))
    for index, buy in enumerate(answers.usual_buys):
        where = f"usual_buys.{index}"
        if buy.brand:
            extra = {"pack_size": buy.size or "", "product_name": buy.product or "", "quantity": str(buy.quantity or "")}
            spec = _spec(where, kind="prefer_brand", item=buy.category, value=buy.brand)
        elif buy.product:
            # A usual buy without a brand ("almonds", "onions").
            extra = {"pack_size": buy.size or "", "quantity": str(buy.quantity or "")}
            spec = _spec(where, kind="usual_item", item=buy.category, value=buy.product)
        elif buy.size:
            extra = {"quantity": str(buy.quantity or "")}
            spec = _spec(where, kind="prefer_pack_size", item=buy.category, value=buy.size)
        elif buy.quantity:
            extra = {}
            spec = _spec(where, kind="usual_quantity", item=buy.category, value=str(buy.quantity))
        else:
            raise SeedError(f"{where}: give at least a brand, a product, a size or a quantity.")
        extra = {key: value for key, value in extra.items() if value}
        category = sensitive_category(*extra.values())
        if category is not None:
            raise SeedError(f"{where}: GharSaathi never remembers {category.replace('_', ' ')} details.")
        wanted.append(Wanted(spec, extra))
    for index, product in enumerate(answers.avoid.products):
        wanted.append(Wanted(_spec(f"avoid.products.{index}", kind="avoid_product", value=product)))
    for index, brand in enumerate(answers.avoid.brands):
        wanted.append(Wanted(_spec(f"avoid.brands.{index}", kind="avoid_brand", value=brand)))
    for index, store in enumerate(answers.avoid.stores):
        wanted.append(Wanted(_spec(f"avoid.stores.{index}", kind="avoid_store", value=store)))
    if answers.value_preference:
        wanted.append(Wanted(_spec("value_preference", kind="shopping_style", value=answers.value_preference)))
    for field_name in ("pack_size_rule", "substitution_rule"):
        rule = getattr(answers, field_name)
        if rule:
            wanted.append(Wanted(_spec(field_name, kind="rule", value=rule)))
    if answers.usual_order_times:
        times = ", ".join(answers.usual_order_times)
        wanted.append(Wanted(_spec("usual_order_times", kind="routine", value=f"Usually orders in the {times}")))
    for index, goal in enumerate(answers.goals):
        wanted.append(Wanted(_spec(f"goals.{index}", kind="goal", value=goal)))
    for index, routine in enumerate(answers.routines):
        wanted.append(Wanted(_spec(f"routines.{index}", kind="routine", value=routine)))
    if answers.preferred_store:
        wanted.append(Wanted(_spec("preferred_store", kind="prefer_store", value=answers.preferred_store)))
    for index, note in enumerate(answers.delivery_notes):
        if mentions_secret(note):
            raise SeedError(f"delivery_notes.{index}: delivery notes can't contain OTPs, PINs, passwords or payment details.")
        wanted.append(Wanted(_spec(f"delivery_notes.{index}", kind="delivery_note", value=note)))

    for index, item in enumerate(wanted):
        category = sensitive_category(*item.spec.policy_texts())
        if category is not None:
            raise SeedError(f"Item {index + 1} ({item.spec.kind}): GharSaathi never remembers {category.replace('_', ' ')} details.")
    seen: dict[tuple[str, str], int] = {}
    for index, item in enumerate(wanted):
        if item.key() in seen:
            raise SeedError(f"Two answers for the same thing ({item.spec.kind}{f' · {item.spec.item}' if item.spec.item else ''}); keep one.")
        seen[item.key()] = index
    return wanted


Action = Literal["create", "update", "unchanged", "remove"]


@dataclass
class PlanStep:
    action: Action
    text: str
    wanted: Wanted | None = None
    existing: MemoryItem | None = None


@dataclass
class Plan:
    steps: list[PlanStep]
    profile_changes: dict[str, str]
    consent_on: bool

    def counts(self) -> dict[str, int]:
        counts = {"create": 0, "update": 0, "unchanged": 0, "remove": 0}
        for step in self.steps:
            counts[step.action] += 1
        return counts


def _existing_key(memory: MemoryItem) -> tuple[str, str] | None:
    kind = kind_of(memory)
    if kind is None or not isinstance(memory.value, dict):
        return None
    item = str(memory.value.get("product_category") or "").casefold()
    if kind in SINGLE_KINDS:
        return kind, item
    if kind == "rule":
        return kind, _rule_family(str(memory.value.get("rule") or ""))
    main = str(memory.value.get(VALUE_KEY_BY_KIND[kind]) or "").casefold()
    return kind, f"{item}|{main}"


def _comparable(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in _IGNORED_KEYS}


async def build_plan(
    session: AsyncSession, actor: MemorySubject | None, external_id: str, answers: DemoMemoryFile | None
) -> Plan:
    """What --apply would do. Reads only."""
    wanted = wanted_items(answers) if answers is not None else []
    memories: list[MemoryItem] = []
    consent = False
    if actor is not None:
        service = HouseholdPreferenceService(session)
        memories = await service.memories(actor, {MemoryStatus.CONFIRMED})
        consent = await service.consent_on(actor)
    by_key: dict[tuple[str, str], MemoryItem] = {}
    seeded_by_key: dict[tuple[str, str], MemoryItem] = {}
    for memory in memories:
        key = _existing_key(memory)
        if key is None:
            continue
        if memory.source_conversation_id == SEED_MARKER:
            seeded_by_key.setdefault(key, memory)
        else:
            by_key.setdefault(key, memory)
    steps: list[PlanStep] = []
    used: set[tuple[str, str]] = set()
    for item in wanted:
        key = item.key()
        used.add(key)
        existing = seeded_by_key.get(key) or by_key.get(key)
        if existing is None:
            steps.append(PlanStep("create", item.text, item))
        elif existing.memory_type == item.spec.memory_type and _comparable(existing.value) == _comparable(item.value):
            steps.append(PlanStep("unchanged", item.text, item, existing))
        else:
            steps.append(PlanStep("update", f"{existing.text} → {item.text}", item, existing))
    for key, memory in seeded_by_key.items():
        if key not in used:
            steps.append(PlanStep("remove", memory.text, None, memory))
    for memory in memories:
        if memory.source_conversation_id == SEED_MARKER and memory not in seeded_by_key.values():
            # A second tagged item for the same key (should not happen): remove it too.
            steps.append(PlanStep("remove", memory.text, None, memory))
    profile_changes: dict[str, str] = {}
    if answers is not None and (answers.language or answers.address_form):
        record = await get_profile_record(session, external_id)
        if answers.language and (record is None or record.language != answers.language):
            profile_changes["language"] = answers.language
        if answers.address_form and (record is None or record.address_form != answers.address_form):
            profile_changes["address_form"] = answers.address_form
    return Plan(steps, profile_changes, consent)


# Apply -----------------------------------------------------------------------


async def _tag(session: AsyncSession, memory: MemoryItem) -> MemoryItem:
    memory.source_conversation_id = SEED_MARKER
    await session.commit()
    await session.refresh(memory)
    return memory


async def apply_plan(
    session: AsyncSession, actor: MemorySubject, external_id: str, plan: Plan, *, turn_on_memory: bool = False
) -> dict[str, int]:
    service = HouseholdPreferenceService(session)
    if plan.steps and not await service.consent_on(actor):
        if not turn_on_memory:
            raise SeedError(
                "Saved preferences are off for this user. Turn them on in Settings → Saved "
                "preferences, or pass --turn-on-memory."
            )
        await service.set_consent(actor, True)
    memory = MemoryService(session)
    try:
        for step in plan.steps:
            if step.action == "create" and step.wanted is not None:
                created, _ = await service.propose(
                    actor, step.wanted.spec, origin="explicit", conversation_id=SEED_MARKER, extra_value=step.wanted.extra
                )
                if created.status == MemoryStatus.PROPOSED:
                    created = await memory.confirm(actor, created.id)
                await _tag(session, created)
            elif step.action == "update" and step.wanted is not None and step.existing is not None:
                await service.replace(
                    actor,
                    step.existing,
                    step.wanted.spec,
                    origin="explicit",
                    reason="Owner's household answers (manual seed).",
                    conversation_id=SEED_MARKER,
                    extra_value=step.wanted.extra,
                    source=MemorySourceType.EXPLICIT_STATEMENT,
                )
            elif step.action == "remove" and step.existing is not None:
                await _forget_seeded(session, actor, step.existing)
    except MemoryPolicyError as exc:
        raise SeedError(str(exc)) from exc
    if plan.profile_changes:
        await update_profile(session, external_id, UserProfileUpdate(**plan.profile_changes))  # type: ignore[arg-type]
    return plan.counts()


async def _forget_seeded(session: AsyncSession, actor: MemorySubject, memory: MemoryItem) -> None:
    """Delete one seeded memory; restore an earlier non-seeded one it had replaced."""
    repository = MemoryRepository(session)
    if memory.supersedes_memory_id is not None:
        previous = await repository.get_memory(memory.supersedes_memory_id, actor.id)
        if (
            previous is not None
            and previous.status == MemoryStatus.SUPERSEDED
            and previous.source_conversation_id != SEED_MARKER
        ):
            previous.status = MemoryStatus.CONFIRMED
            repository.add_event(previous, MemoryEventType.CORRECTED, actor.id, "Restored: seeded memory removed.")
    await MemoryService(session).forget(actor, memory.id)


async def remove_seeded(session: AsyncSession, actor: MemorySubject, *, apply: bool) -> int:
    """Delete exactly the tagged items (every status but deleted); returns how many."""
    rows = (
        await session.execute(
            select(MemoryItem).where(
                MemoryItem.subject_id == actor.id,
                MemoryItem.source_conversation_id == SEED_MARKER,
                MemoryItem.status.in_([MemoryStatus.CONFIRMED, MemoryStatus.PROPOSED]),
            )
        )
    ).scalars().all()
    if apply:
        for memory in rows:
            await _forget_seeded(session, actor, memory)
    return len(rows)


# Who -------------------------------------------------------------------------


def mask(user_id: str) -> str:
    if len(user_id) <= 8:
        return user_id[:2] + "…"
    return f"{user_id[:6]}…{user_id[-2:]}"


async def resolve_user(session: AsyncSession, user_id: str | None) -> tuple[str, MemorySubject | None]:
    """The owner's user id and memory subject (None if the app never made one yet)."""
    repository = MemoryRepository(session)
    if user_id:
        actor = await repository.find_person(user_id)
        if actor is None and await get_profile_record(session, user_id) is None:
            raise SeedError(f"No signed-in user {mask(user_id)} here. Sign in to the app once first.")
        return user_id, actor
    profiles = (await session.execute(select(UserProfileRecord.user_id))).scalars().all()
    candidates = list(dict.fromkeys(profiles))
    if not candidates:
        candidates = list(
            (
                await session.execute(
                    select(MemorySubject.external_id).where(MemorySubject.subject_type == SubjectType.PERSON)
                )
            ).scalars().all()
        )
    if len(candidates) != 1:
        listed = ", ".join(mask(candidate) for candidate in candidates) or "none"
        raise SeedError(f"Pass --user-id; users found: {listed}.")
    return candidates[0], await repository.find_person(candidates[0])


# CLI -------------------------------------------------------------------------

_SYMBOLS = {"create": "+", "update": "~", "unchanged": "=", "remove": "-"}


def render_plan(plan: Plan, *, show_items: bool = True) -> list[str]:
    counts = plan.counts()
    lines = [
        f"create {counts['create']}, update {counts['update']}, unchanged {counts['unchanged']}, "
        f"remove {counts['remove']}; profile fields to change: {len(plan.profile_changes)}; "
        f"saved preferences: {'on' if plan.consent_on else 'off'}"
    ]
    if show_items:
        lines += [f"  {_SYMBOLS[step.action]} {step.text}" for step in plan.steps]
        lines += [f"  ~ profile {key}: {value}" for key, value in plan.profile_changes.items()]
    return lines


async def run(args: argparse.Namespace, session: AsyncSession) -> list[str]:
    external_id, actor = await resolve_user(session, args.user_id)
    out = [f"user={mask(external_id)}"]
    if args.remove_seeded:
        if actor is None:
            return [*out, "Nothing seeded for this user."]
        count = await remove_seeded(session, actor, apply=args.apply)
        verb = "Removed" if args.apply else "Would remove"
        return [*out, f"{verb} {count} seeded memories. Language and address form are unchanged."]
    if not args.file:
        raise SeedError("Pass --file <path> (or --remove-seeded).")
    answers = load_file(Path(args.file))
    plan = await build_plan(session, actor, external_id, answers)
    if not args.apply:
        return [*out, "Dry run: nothing written.", *render_plan(plan)]
    if actor is None:
        actor = await MemoryService(session).actor_for_external_id(external_id)
    await apply_plan(session, actor, external_id, plan, turn_on_memory=args.turn_on_memory)
    return [*out, "Applied.", *render_plan(plan)]


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(
        prog="make memory-seed ARGS=...",
        description="Seed the owner's own household memory from a YAML/JSON file (dry run by default).",
    )
    cli.add_argument("--file", help="YAML or JSON answers file (see docs/demo-memory.example.yaml).")
    cli.add_argument("--user-id", help="Clerk user id; defaults to the only signed-in user.")
    mode = cli.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Print the plan only (default).")
    mode.add_argument("--apply", action="store_true", help="Write the plan.")
    cli.add_argument("--remove-seeded", action="store_true", help="Delete exactly the items this tool seeded.")
    cli.add_argument("--turn-on-memory", action="store_true", help="Turn saved preferences on for this user if off.")
    return cli


async def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    from app.database.session import get_session_factory

    try:
        async with get_session_factory()() as session:
            for line in await run(args, session):
                print(line)
    except SeedError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    asyncio.run(main())
