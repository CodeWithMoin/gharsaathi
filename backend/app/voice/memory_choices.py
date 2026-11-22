"""Session hooks that connect basket adds to household memory.

- ``blocked_add``: an option from a store the household said never to use is
  not added (it can only come from a search made before the rule existed).
- ``after_add``: records the choice for repeated-choice suggestions (tier 2) and,
  at most once per session, attaches one matching pending suggestion so the
  assistant can ask whether to remember it.
"""

from __future__ import annotations

import json
import logging
import secrets
from app.memory.choice_signals import generic_item
from app.memory.household import STORE_LABELS
from app.memory.household_rules import avoided_store_rule, rules_from_memory, store_rule_label
from app.memory.household_writer import ChoiceRecord, HouseholdPreferenceWriter, MemoryView
from app.memory.recall import item_terms, term_matcher
from app.memory.remembered import name_tokens, names_match, pack_key
from app.voice.memory_write_tools import MemoryWriteToolExecutor
from app.voice.tools import MultiProviderShoppingToolExecutor, SearchOption, ToolOutcome

logger = logging.getLogger(__name__)

SUGGESTION_NOTE = (
    "This household has chosen this same thing repeatedly. After confirming the add, you may "
    "ask once, briefly, in the user's reply language, whether to remember it (the summary). "
    "Call confirm_memory with confirm only on a clear yes, reject on no. Do not ask again "
    "this session."
)


def _option_id(raw_arguments: str) -> str | None:
    try:
        arguments = json.loads(raw_arguments)
    except (json.JSONDecodeError, TypeError):
        return None
    value = arguments.get("option_id") if isinstance(arguments, dict) else None
    return value if isinstance(value, str) else None


class MemoryChoiceHooks:
    def __init__(
        self,
        writer: HouseholdPreferenceWriter | None,
        memory_tools: MemoryWriteToolExecutor,
        search: MultiProviderShoppingToolExecutor,
        options: dict[str, SearchOption],
        household_id: str,
    ) -> None:
        self.writer = writer
        self.memory_tools = memory_tools
        self.search = search
        self.options = options
        self.household_id = household_id
        self.session_key = f"session:{secrets.token_hex(8)}"
        self.last_query = ""
        self.suggested = False

    def saw_search(self, raw_arguments: str) -> None:
        try:
            arguments = json.loads(raw_arguments)
        except (json.JSONDecodeError, TypeError):
            return
        query = arguments.get("query") if isinstance(arguments, dict) else None
        if isinstance(query, str):
            self.last_query = " ".join(query.split())[:120]

    async def blocked_add(self, raw_arguments: str) -> ToolOutcome | None:
        option_id = _option_id(raw_arguments)
        option = self.options.get(option_id or "")
        if option is None:
            return None
        memory = await self.search._household_memory()
        rule = avoided_store_rule(
            rules_from_memory(memory), option.provider, f"{self.last_query} {option.product.name}"
        )
        if rule is None:
            return None
        return ToolOutcome(
            model_output={
                "status": "blocked_by_household_rule",
                "store": STORE_LABELS.get(option.provider, option.provider),
                "household_rule": store_rule_label(rule),
                "message": (
                    "Not added: this household asked never to order from this store. Say so in "
                    "one line and offer another store's option or to change the rule."
                ),
            }
        )

    async def after_add(self, raw_arguments: str, outcome: ToolOutcome) -> ToolOutcome:
        if self.writer is None or outcome.model_output.get("status") != "added":
            return outcome
        option = self.options.get(_option_id(raw_arguments) or "")
        if option is None:
            return outcome
        try:
            await self._record(option)
            suggestion = await self._suggestion(option)
        except Exception as exc:  # noqa: BLE001 - memory must never break a basket add
            logger.warning("event=memory_choice_hook_failed error=%s", type(exc).__name__)
            return outcome
        if suggestion is None:
            return outcome
        self.suggested = True
        return outcome.model_copy(
            update={
                "model_output": {
                    **outcome.model_output,
                    "memory_suggestion": {**self.memory_tools.entry(suggestion), "note": SUGGESTION_NOTE},
                }
            }
        )

    async def _record(self, option: SearchOption) -> None:
        assert self.writer is not None
        product = option.product
        size = pack_key(f"{product.pack_size or ''} {product.name}")
        competing = sorted(
            {
                other.provider
                for other in self.options.values()
                if other.provider != option.provider
                and other.product.available
                and pack_key(f"{other.product.pack_size or ''} {other.product.name}") == size
                and names_match(
                    f"{other.product.brand or ''} {other.product.name}",
                    f"{product.brand or ''} {product.name}",
                )
            }
        )
        occasion = await self.writer.current_conversation(self.household_id) or self.session_key
        await self.writer.record_choice(
            self.household_id,
            ChoiceRecord(
                occasion_key=occasion,
                provider=option.provider,
                item=generic_item(self.last_query or product.name, product.name, product.brand),
                product_name=product.name,
                brand=product.brand,
                pack_size=product.pack_size,
                competing_providers=competing,
            ),
        )

    async def _suggestion(self, option: SearchOption) -> MemoryView | None:
        assert self.writer is not None
        if self.suggested:
            return None
        product = option.product
        haystack = f"{product.brand or ''} {product.name}"
        for memory in await self.writer.list(self.household_id):
            if memory.status != "proposed" or memory.origin != "inferred" or not memory.value:
                continue
            terms = item_terms(memory.item or "")
            if memory.item and terms and not term_matcher(terms)(f"{haystack} {self.last_query}"):
                continue
            brand = name_tokens(memory.value)
            if memory.kind == "prefer_brand" and brand and brand <= name_tokens(haystack):
                return memory
            if memory.kind == "prefer_store" and memory.value == option.provider:
                return memory
        return None

