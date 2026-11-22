"""``gs eval``: run the evaluation scripts with one summary table and a local history.

Each script keeps its own output; this module only runs it as
``python -m evaluation.<module>`` from ``backend/``, redirects reports that
would overwrite a tracked ``evaluation/*_report.json`` into
``.run/evals/reports/``, and reads pass/total and latency back from the report
or stdout. Results are appended to ``.run/evals/history.jsonl`` (gitignored).
"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.core.config import REPOSITORY_ROOT

Needs = Literal["offline", "openai", "jev", "audio"]


@dataclass(frozen=True)
class EvalSpec:
    name: str
    module: str
    needs: Needs
    description: str
    report_flag: bool = False
    args: tuple[str, ...] = ()
    # Manual evals generate audio or long live sessions; `gs eval all` skips them.
    manual: bool = False

    @property
    def quick(self) -> bool:
        return self.needs == "offline"


EVALS: tuple[EvalSpec, ...] = (
    EvalSpec("relevance", "run_relevance_benchmark", "offline", "query guard + ranking regression cases", report_flag=True),
    EvalSpec("recommender", "run_recommender", "offline", "personal recommender checks"),
    EvalSpec("instamart-workflows", "run_instamart_workflows", "offline", "Instamart workflow tool coverage", report_flag=True),
    EvalSpec("turn-latency", "run_turn_latency", "offline", "offline per-stage latency with fake stores", report_flag=True, args=("--runs", "1")),
    EvalSpec("voice-diversity", "run_voice_diversity", "offline", "spoken-reply diversity on mock catalogue", report_flag=True),
    EvalSpec("voice-prompt", "run_voice_prompt", "openai", "GPT-Live prompt replies (text simulation)"),
    EvalSpec("voice-delegation", "run_voice_delegation", "openai", "multi-turn backend brain scenarios", report_flag=True),
    EvalSpec("voice-memory-recall", "run_voice_memory_recall", "openai", "memory recall before search", report_flag=True),
    EvalSpec("typed-chat", "run_typed_chat", "openai", "typed /api/chat/turn loop", report_flag=True),
    EvalSpec("failure-scenarios", "run_failure_scenarios", "openai", "assistant behaviour under failures", report_flag=True),
    EvalSpec("rider-prompt", "run_rider_prompt", "openai", "rider assist prompt rules", report_flag=True),
    EvalSpec("memory-extraction", "run_memory_extraction", "openai", "memory extraction exact-match"),
    EvalSpec("photo-extraction", "run_photo_extraction", "openai", "photo pack-name reading"),
    EvalSpec("jev-relevance", "run_jev_relevance", "jev", "Jev reranker on labelled shortlist"),
    EvalSpec("realtime-voice", "run_realtime_voice", "audio", "Realtime audio generation (writes frontend/public)", manual=True, report_flag=True),
    EvalSpec("turn-taking", "run_turn_taking", "audio", "turn-taking clips (macOS say + ffmpeg)", manual=True),
)


def find(name: str) -> EvalSpec:
    for spec in EVALS:
        if spec.name == name or spec.module == name or spec.module == f"run_{name.replace('-', '_')}":
            return spec
    raise KeyError(name)


def selection(*, quick: bool) -> list[EvalSpec]:
    return [spec for spec in EVALS if not spec.manual and (spec.quick or not quick)]


@dataclass
class EvalResult:
    name: str
    passed: int | None
    total: int | None
    exit_code: int
    wall_ms: float
    median_ms: float | None
    report: str | None = None
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and (self.total is None or self.passed == self.total)


@dataclass
class EvalRun:
    run_id: str
    at: str
    quick: bool
    commit: str | None
    results: list[EvalResult] = field(default_factory=list)


_PASS_LINE = re.compile(r"(?:^|:\s)(PASS|FAIL)\b", re.MULTILINE)
_LATENCY_KEYS = ("latency_ms", "total_ms", "elapsed_ms", "duration_ms")


def _json_blocks(text: str) -> list[Any]:
    """Every top-level JSON object printed on stdout (scripts print one pretty block)."""

    blocks: list[Any] = []
    decoder = json.JSONDecoder()
    index = 0
    while True:
        start = text.find("{", index)
        if start < 0:
            return blocks
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            index = start + 1
            continue
        blocks.append(value)
        index = end


def _runs(document: Any) -> list[dict[str, Any]]:
    runs = document.get("runs") if isinstance(document, dict) else None
    if not isinstance(runs, list):
        return []
    flat: list[dict[str, Any]] = []
    for entry in runs:
        if isinstance(entry, list):
            flat.extend(item for item in entry if isinstance(item, dict))
        elif isinstance(entry, dict):
            flat.append(entry)
    return flat


def _latencies(runs: Sequence[dict[str, Any]]) -> list[float]:
    values: list[float] = []
    for run in runs:
        seconds = run.get("seconds")
        if isinstance(seconds, int | float) and not isinstance(seconds, bool):
            values.append(float(seconds) * 1000)
            continue
        for key in _LATENCY_KEYS:
            value = run.get(key)
            if isinstance(value, int | float) and not isinstance(value, bool):
                values.append(float(value))
                break
    return values


def summarize(stdout: str, report: Any, exit_code: int) -> tuple[int | None, int | None, float | None, str]:
    """(passed, total, median latency ms, note) from whatever the script produced."""

    documents = [doc for doc in [report, *reversed(_json_blocks(stdout))] if isinstance(doc, dict)]
    runs = next((_runs(doc) for doc in documents if _runs(doc)), [])
    median = statistics.median(_latencies(runs)) if _latencies(runs) else None
    for doc in documents:
        passed, total = doc.get("passed"), doc.get("total")
        if isinstance(passed, int) and isinstance(total, int) and not isinstance(passed, bool):
            return passed, total, median, ""
    if runs and all(isinstance(run.get("passed"), bool) for run in runs):
        return sum(1 for run in runs if run["passed"]), len(runs), median, ""
    marks = _PASS_LINE.findall(stdout)
    if marks:
        return marks.count("PASS"), len(marks), median, ""
    for doc in documents:
        if doc.get("status") == "skipped":
            return None, None, median, str(doc.get("reason", "skipped"))[:80]
        if isinstance(doc.get("errors"), list):
            return (0 if doc["errors"] else 1), 1, median, ""
    return (1 if exit_code == 0 else 0), 1, median, "exit code only"


Runner = Callable[[list[str], Path], tuple[int, str, str]]


def run_process(argv: list[str], cwd: Path) -> tuple[int, str, str]:
    process = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)
    return process.returncode, process.stdout, process.stderr


def run_eval(
    spec: EvalSpec,
    *,
    reports_dir: Path,
    runner: Runner = run_process,
    python: str = sys.executable,
    backend_dir: Path = REPOSITORY_ROOT / "backend",
    stream: Callable[[str], None] | None = None,
) -> EvalResult:
    argv = [python, "-m", f"evaluation.{spec.module}", *spec.args]
    report_path: Path | None = None
    if spec.report_flag:
        reports_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        report_path = reports_dir / f"{spec.name}-{stamp}.json"
        argv += ["--report", str(report_path)]
    started = time.perf_counter()
    code, stdout, stderr = runner(argv, backend_dir)
    wall = round((time.perf_counter() - started) * 1000, 1)
    if stream is not None:
        meaningful = [line.strip() for line in stdout.splitlines() if len(line.strip().strip("{}[],")) > 2]
        tail = meaningful[-1][:100] if meaningful else ""
        stream(f"  {spec.name}: exit {code}" + (f" · {tail}" if tail else ""))
    report: Any = None
    if report_path is not None and report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = None
    passed, total, median, note = summarize(stdout, report, code)
    if code != 0 and not note:
        lines = (stderr.strip() or stdout.strip()).splitlines()
        note = lines[-1][:100] if lines else f"exit {code}"
    return EvalResult(
        spec.name,
        passed,
        total,
        code,
        wall,
        round(median, 1) if median is not None else None,
        str(report_path) if report_path is not None and report_path.exists() else None,
        note,
    )


# History -----------------------------------------------------------------------
def append_history(path: Path, run: EvalRun) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({**asdict(run)}, ensure_ascii=False) + "\n")


def load_history(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    runs = []
    for line in lines:
        try:
            runs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return runs


def diff_runs(previous: dict[str, Any], latest: dict[str, Any]) -> list[dict[str, Any]]:
    """Per eval: pass/total and median latency in the previous and latest run."""

    def index(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {result["name"]: result for result in run.get("results", [])}

    before, after = index(previous), index(latest)
    rows = []
    for name in [*after, *(name for name in before if name not in after)]:
        old, new = before.get(name), after.get(name)

        def score(result: dict[str, Any] | None) -> str:
            if result is None:
                return "—"
            if result.get("total") is None:
                return "skipped"
            return f"{result['passed']}/{result['total']}"

        def latency(result: dict[str, Any] | None) -> float | None:
            if result is None:
                return None
            return result.get("median_ms") if result.get("median_ms") is not None else result.get("wall_ms")

        change = "same"
        if old is None:
            change = "new"
        elif new is None:
            change = "missing"
        elif score(old) != score(new):
            rate = lambda r: (r["passed"] / r["total"]) if r.get("total") else 0.0  # noqa: E731
            change = "better" if rate(new) > rate(old) else "worse"
        rows.append(
            {
                "name": name,
                "before": score(old),
                "after": score(new),
                "before_ms": latency(old),
                "after_ms": latency(new),
                "change": change,
            }
        )
    return rows


def median_of(results: Iterable[EvalResult]) -> float | None:
    values = [r.median_ms if r.median_ms is not None else r.wall_ms for r in results]
    return round(statistics.median(values), 1) if values else None
