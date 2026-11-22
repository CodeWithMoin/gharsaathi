"""Opt-in turn traces: redaction, rotation, opt-in, and the record shape."""

import json
from pathlib import Path
from types import SimpleNamespace

from app.core.config import Settings
from app.devtools.trace import (
    REDACTED,
    TraceWriter,
    TurnTrace,
    read_traces,
    start_trace,
    summarize_arguments,
    summarize_result,
    usage_of,
)


def test_tracing_is_off_by_default(tmp_path: Path) -> None:
    assert Settings(_env_file=None).dev_trace is False
    assert start_trace(Settings(_env_file=None, dev_trace_dir=tmp_path), "typed") is None
    assert not (tmp_path / "turns.jsonl").exists()


def test_arguments_keep_short_shopping_values_and_redact_sensitive_ones() -> None:
    summary = summarize_arguments(
        "search_products",
        json.dumps(
            {
                "query": "Amul Taaza toned milk 1 litre pouch pack of two for the family",
                "quantity": 2,
                "spin_id": "spin-1",
                "contact_number": "9876543210",
                "confirm_token": "tok_secret_value",
                "pin": "1234",
                "note": "+91 98765 43210",
                "items": [{"a": 1}, {"b": 2}],
                "filters": {"brand": "Amul"},
            }
        ),
    )
    assert summary["query"].endswith("…") and len(summary["query"]) == 40
    assert summary["quantity"] == 2
    assert summary["spin_id"] == "spin-1"
    for key in ("contact_number", "confirm_token", "pin", "note"):
        assert summary[key] == REDACTED
    assert summary["items"] == "[2 items]"
    assert summary["filters"] == "{brand}"
    assert "9876543210" not in json.dumps(summary)


def test_address_form_text_is_never_written() -> None:
    summary = summarize_arguments("open_address_form", {"building_name": "RMZ The Loft", "short_address": "Gachibowli"})
    assert set(summary.values()) == {REDACTED}


def test_unparseable_arguments_and_results_stay_content_free() -> None:
    assert summarize_arguments("x", "{not json") == {"_unparsed_chars": 9}
    status, counts = summarize_result({"status": "ok", "options": [{"name": "secret product"}] * 3, "message": "hi"})
    assert (status, counts) == ("ok", {"options": 3})
    assert summarize_result(None) == ("none", {})


def test_usage_from_sdk_objects_and_dicts() -> None:
    sdk = SimpleNamespace(
        input_tokens=100,
        output_tokens=7,
        input_tokens_details=SimpleNamespace(cached_tokens=90),
        output_tokens_details=SimpleNamespace(reasoning_tokens=3),
    )
    assert usage_of(sdk) == {"input": 100, "output": 7, "cached": 90, "reasoning": 3}
    assert usage_of({"input_tokens": 5}) == {"input": 5}
    assert usage_of(None) == {}


def test_record_has_rounds_tools_totals_and_no_output_text() -> None:
    written: list[dict] = []
    trace = TurnTrace("typed", model="m", prompt_version="1.0", conversation_id="c1", sink=written.append)
    trace.model_round(1200.0, {"input_tokens": 10, "output_tokens": 2})
    trace.tool("search_products", '{"query": "atta"}', {"status": "ok", "options": [1, 2], "message": "DO NOT STORE"}, 300.0, stages={"zepto": 280.0})
    trace.model_round(800.0, {"input_tokens": 12, "output_tokens": 5})
    record = trace.finish("ok")
    assert written == [record]
    assert record["totals"] == {"rounds": 2, "tools": 1, "model_ms": 2000.0, "tool_ms": 300.0, "tokens": {"input": 22, "output": 7}}
    tool = record["rounds"][0]["tools"][0]
    assert tool == {"name": "search_products", "args": {"query": "atta"}, "status": "ok", "ms": 300.0, "counts": {"options": 2}, "stages": {"zepto": 280.0}}
    assert "DO NOT STORE" not in json.dumps(record)


def test_voice_tools_file_under_their_own_round_even_before_it_completes() -> None:
    trace = TurnTrace("voice", model="m", prompt_version="1")
    trace.tool("search_products", "{}", {"status": "ok"}, 10.0, round_number=1)
    trace.model_round(500.0, round_number=1)
    trace.tool("add_to_basket", "{}", {"status": "added"}, 5.0, round_number=2)
    trace.model_round(400.0, round_number=2)
    record = trace.finish("completed")
    assert [r["model_ms"] for r in record["rounds"]] == [500.0, 400.0]
    assert [r["tools"][0]["name"] for r in record["rounds"]] == ["search_products", "add_to_basket"]


def test_writer_rotates_to_the_newest_records(tmp_path: Path) -> None:
    writer = TraceWriter(tmp_path / "traces", keep=10)
    for index in range(30):
        writer.write({"turn_id": str(index)})
    records = read_traces(tmp_path / "traces")
    assert 10 <= len(records) <= 12
    assert records[-1]["turn_id"] == "29"
    assert (tmp_path / "traces" / "turns.jsonl").stat().st_mode & 0o777 == 0o600


def test_start_trace_writes_when_enabled(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, dev_trace=True, dev_trace_dir=tmp_path / "t", dev_trace_keep=10)
    trace = start_trace(settings, "typed", conversation_id=None)
    assert trace is not None
    trace.model_round(1.0)
    trace.finish("ok")
    [record] = read_traces(tmp_path / "t")
    assert record["channel"] == "typed" and record["model"] == settings.live_backend_model
