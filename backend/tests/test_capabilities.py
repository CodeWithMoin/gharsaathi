"""Guards that keep docs/CAPABILITIES.md honest. Offline: no provider or OpenAI calls.

Every failure message says what to change in ``app/capabilities.py``; after
editing it, regenerate the doc with
``uv run --with-requirements requirements.txt python -m app.capabilities --write``.
"""

import importlib
import pkgutil
import re
from collections import Counter
from pathlib import Path

from fastapi.routing import APIRoute

import app.api as api_package
import app.voice as voice_package
from app import capabilities as registry
from app.capabilities import CAPABILITIES, INFRASTRUCTURE_ROUTES, Status
from app.main import app
from app.voice import prompts
from app.voice.toolbox import VOICE_TOOL_DEFINITIONS

REPO_ROOT = Path(__file__).resolve().parents[2]
REGEN = "then run `python -m app.capabilities --write` from backend/"
VOICE_SURFACE_REQUIRES_TOOL = {Status.WORKS, Status.PARTIAL}


def _walk(routes, prefix=""):
    """Yield (method, path, endpoint) for every mounted API route.

    FastAPI >=0.165 keeps included routers lazily (``original_router`` +
    ``include_context``); older versions flatten them into ``APIRoute``.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            for method in route.methods:
                yield method, prefix + route.path, route.endpoint
        elif hasattr(route, "original_router") and hasattr(route, "include_context"):
            yield from _walk(
                route.original_router.routes, prefix + (route.include_context.prefix or "")
            )


def _app_routes() -> dict[str, object]:
    return {f"{method} {path}": endpoint for method, path, endpoint in _walk(app.routes)}


def _registered_routes() -> list[str]:
    routes = [route for capability in CAPABILITIES for route in capability.routes]
    return routes + list(INFRASTRUCTURE_ROUTES)


def _voice_tool_definition_lists() -> dict[str, list[dict]]:
    """Every module-level ``*TOOL_DEFINITIONS`` list in app.voice, by qualified name."""
    lists: dict[str, list[dict]] = {}
    for module_info in pkgutil.iter_modules(voice_package.__path__):
        module = importlib.import_module(f"app.voice.{module_info.name}")
        for attribute, value in vars(module).items():
            if attribute.endswith("TOOL_DEFINITIONS") and isinstance(value, list):
                lists[f"{module.__name__}.{attribute}"] = value
    return lists


def _voice_tool_names() -> set[str]:
    return {
        tool["name"]
        for definitions in _voice_tool_definition_lists().values()
        for tool in definitions
    }


def test_voice_tool_discovery_finds_the_session_list():
    assert "app.voice.toolbox.VOICE_TOOL_DEFINITIONS" in _voice_tool_definition_lists()


def test_route_walker_sees_every_documented_route():
    walked = set(_app_routes())
    openapi = {
        f"{method.upper()} {path}"
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }
    missing = sorted(openapi - walked)
    assert not missing, (
        "The route walker in this test no longer matches FastAPI's routing internals; "
        f"it missed {missing}. Update _walk()."
    )


def test_every_app_route_is_in_the_registry():
    unlisted = sorted(set(_app_routes()) - set(_registered_routes()))
    assert not unlisted, (
        "These FastAPI routes are not in app/capabilities.py. Add each one to the "
        "`routes` of the capability it serves (or to INFRASTRUCTURE_ROUTES with a "
        f"reason), {REGEN}:\n  " + "\n  ".join(unlisted)
    )


def test_registry_routes_exist_and_are_listed_once():
    stale = sorted(set(_registered_routes()) - set(_app_routes()))
    assert not stale, (
        "app/capabilities.py lists routes the app does not mount. Remove or rename them, "
        f"{REGEN}:\n  " + "\n  ".join(stale)
    )
    duplicates = sorted(route for route, n in Counter(_registered_routes()).items() if n > 1)
    assert not duplicates, f"Routes listed under more than one capability: {duplicates}"


def test_every_api_router_is_mounted_or_explained():
    mounted = {id(endpoint) for endpoint in _app_routes().values()}
    unexplained = []
    for module_info in pkgutil.iter_modules(api_package.__path__):
        name = f"app.api.{module_info.name}"
        router = getattr(importlib.import_module(name), "router", None)
        if router is None or name in registry.UNMOUNTED_ROUTERS:
            continue
        if any(isinstance(r, APIRoute) and id(r.endpoint) not in mounted for r in router.routes):
            unexplained.append(name)
    assert not unexplained, (
        f"Routers with endpoints not mounted in app.main: {unexplained}. Include them in "
        "app/main.py, or add them to UNMOUNTED_ROUTERS in app/capabilities.py with a reason."
    )
    still_listed = [name for name in registry.UNMOUNTED_ROUTERS if any(
        id(r.endpoint) in mounted
        for r in importlib.import_module(name).router.routes
        if isinstance(r, APIRoute)
    )]
    assert not still_listed, f"Now mounted; remove from UNMOUNTED_ROUTERS: {still_listed}"


def test_every_voice_tool_is_in_the_registry_and_vice_versa():
    defined = _voice_tool_names()
    claimed = {tool for capability in CAPABILITIES for tool in capability.voice_tools}
    unclaimed = sorted(defined - claimed)
    assert not unclaimed, (
        "Voice tools defined in app/voice but not claimed by any capability. Add them to "
        f"the `voice_tools` of the capability they deliver and set its voice status, {REGEN}: "
        f"{unclaimed}"
    )
    phantom = sorted(claimed - defined)
    assert not phantom, (
        "app/capabilities.py claims voice tools that no tool definition list declares. "
        f"Remove them from `voice_tools` or define the tool: {phantom}"
    )


def test_session_tool_list_contains_every_voice_tool():
    # The Realtime session is configured with VOICE_TOOL_DEFINITIONS; a tool
    # defined elsewhere but missing here is unreachable by voice.
    session = {tool["name"] for tool in VOICE_TOOL_DEFINITIONS}
    missing = sorted(_voice_tool_names() - session)
    assert not missing, f"Defined but not sent to the voice session: {missing}"


def test_voice_capabilities_that_work_have_a_session_tool():
    session = {tool["name"] for tool in VOICE_TOOL_DEFINITIONS}
    broken = [
        capability.id
        for capability in CAPABILITIES
        if capability.voice.status in VOICE_SURFACE_REQUIRES_TOOL
        and not capability.voice_session_behavior
        and not (capability.voice_tools and set(capability.voice_tools) <= session)
    ]
    assert not broken, (
        "These capabilities claim voice works/partial but have no voice tool in the session "
        f"tool list. Add the tool name to `voice_tools` or downgrade the voice status: {broken}"
    )


def test_voice_prompts_only_reference_defined_tools():
    defined = _voice_tool_names()
    texts = {
        name: value
        for name, value in vars(prompts).items()
        if name.isupper() and isinstance(value, str)
    }
    identifier = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
    unknown = sorted(
        {
            f"{name}: {token}"
            for name, text in texts.items()
            for token in identifier.findall(text)
            if token not in defined and token not in registry.PROMPT_NON_TOOL_IDENTIFIERS
        }
    )
    assert not unknown, (
        "Voice prompts mention snake_case names that are not defined voice tools. Define the "
        "tool, fix the typo, or (if it is a tool result field/status) add it to "
        f"PROMPT_NON_TOOL_IDENTIFIERS in app/capabilities.py: {unknown}"
    )


def test_registry_entries_are_well_formed():
    ids = [capability.id for capability in CAPABILITIES]
    assert len(ids) == len(set(ids)), "Capability ids must be unique."
    for capability in CAPABILITIES:
        for key, _ in registry.SURFACES:
            surface = getattr(capability, key)
            if surface.status is Status.IN_PROGRESS:
                assert surface.branch, f"{capability.id}.{key} is in progress; set `branch`."
            if surface.status in {Status.MISSING, Status.UNSUPPORTED, Status.PARTIAL}:
                assert surface.note, f"{capability.id}.{key} needs a `note` explaining why."
        if capability.voice_session_behavior:
            assert not capability.voice_tools, (
                f"{capability.id} is voice-session behaviour; move tools to a tool-backed capability."
            )
        elif capability.backend.status in VOICE_SURFACE_REQUIRES_TOOL and not capability.routes:
            assert capability.voice_tools, f"{capability.id}: backend works but lists no routes or tools."
        for path in capability.ui_files:
            assert (REPO_ROOT / path).is_file(), f"{capability.id}: ui_files entry {path} does not exist."


def test_capabilities_doc_matches_registry():
    text = registry.DOC_PATH.read_text(encoding="utf-8")
    start = text.find(registry.BEGIN_MARKER)
    end = text.find(registry.END_MARKER)
    assert start != -1 and end != -1, "docs/CAPABILITIES.md lost its generated-block markers."
    generated = text[start : end + len(registry.END_MARKER)]
    assert generated == registry.render_markdown(), (
        "docs/CAPABILITIES.md is out of date with app/capabilities.py. Run "
        "`uv run --with-requirements requirements.txt python -m app.capabilities --write` from backend/."
    )
