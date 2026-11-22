"""``gs eval`` result parsing and run comparison (no script is run here)."""

import json

from app.devtools.evals import EVALS, diff_runs, find, selection, summarize


def test_quick_selection_is_offline_only_and_all_skips_manual_audio_evals() -> None:
    assert {spec.needs for spec in selection(quick=True)} == {"offline"}
    assert not any(spec.manual for spec in selection(quick=False))
    assert find("relevance").module == "run_relevance_benchmark"
    assert find("run_typed_chat").name == "typed-chat"
    assert len({spec.name for spec in EVALS}) == len(EVALS)


def test_summary_from_json_on_stdout() -> None:
    stdout = "noise\n" + json.dumps({"suite": "x", "passed": 10, "total": 11, "cases": []}, indent=2)
    assert summarize(stdout, None, 1)[:2] == (10, 11)


def test_summary_from_report_runs_with_latency() -> None:
    report = {"runs": [{"id": "a", "passed": True, "seconds": 2.0}, {"id": "b", "passed": False, "seconds": 4.0}]}
    passed, total, median, _ = summarize("Report: x", report, 0)
    assert (passed, total, median) == (1, 2, 3000.0)


def test_summary_from_pass_fail_lines_and_nested_runs() -> None:
    stdout = "run 1 a: PASS\nrun 1 b: FAIL no otp\nrun 2 a: PASS\n"
    report = {"runs": [[{"id": "a", "failures": []}]], "passed": False}
    assert summarize(stdout, report, 1)[:2] == (2, 3)


def test_summary_from_skip_errors_and_exit_code() -> None:
    assert summarize(json.dumps({"status": "skipped", "reason": "OPENAI_API_KEY is not configured"}), None, 0)[:2] == (None, None)
    assert summarize(json.dumps({"errors": []}), None, 0)[:2] == (1, 1)
    assert summarize("", None, 3) == (0, 1, None, "exit code only")


def test_diff_marks_better_worse_new_and_missing() -> None:
    before = {"results": [{"name": "a", "passed": 5, "total": 5, "median_ms": 100, "wall_ms": 1}, {"name": "b", "passed": 1, "total": 2, "wall_ms": 50}, {"name": "gone", "passed": 1, "total": 1}]}
    after = {"results": [{"name": "a", "passed": 4, "total": 5, "median_ms": 90, "wall_ms": 1}, {"name": "b", "passed": 2, "total": 2, "wall_ms": 40}, {"name": "c", "passed": 1, "total": 1}]}
    rows = {row["name"]: row for row in diff_runs(before, after)}
    assert rows["a"]["change"] == "worse" and rows["a"]["before_ms"] == 100
    assert rows["b"]["change"] == "better" and rows["b"]["after_ms"] == 40
    assert rows["c"]["change"] == "new"
    assert rows["gone"]["change"] == "missing"
