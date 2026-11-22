"""docs/FAILURE_MODES.md stays consistent with the tests it names (no I/O beyond reading files)."""

import re
from pathlib import Path

import pytest

from tests.conftest import FAILURE_MODES_DOC, failure_mode_test_refs

pytestmark = pytest.mark.failure_mode

BACKEND = Path(__file__).resolve().parents[2]
FRONTEND_TESTS = BACKEND.parent / "frontend" / "tests" / "unit" / "failure-modes.test.mjs"
SCENARIOS = BACKEND / "evaluation" / "failure_scenarios.json"
FAILURE_TEST_FILES = sorted((BACKEND / "tests" / "module").glob("test_failure_modes_*.py"))
STATUSES = ("covered", "xfail", "manual", "gap")


def rows() -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for line in FAILURE_MODES_DOC.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\| (FM-[A-Z]+\d+) \|", line)
        if not match:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split(" | ")]
        assert match.group(1) not in found, f"duplicate row {match.group(1)}"
        assert len(cells) == 9, f"{match.group(1)} has {len(cells)} columns, expected 9"
        found[match.group(1)] = {"tests": cells[7], "status": cells[8]}
    return found


def test_every_row_has_a_status_and_tests_unless_manual() -> None:
    catalogue = rows()
    assert len(catalogue) >= 60
    for row_id, row in catalogue.items():
        assert row["status"].startswith(STATUSES), f"{row_id}: status {row['status']!r}"
        if not row["status"].startswith(("manual", "gap")):
            assert row["tests"] not in {"", "—"}, f"{row_id} is {row['status']} but names no test"


def test_every_backend_reference_exists() -> None:
    for ref in failure_mode_test_refs():
        path, _, prefix = ref.partition("::")
        source = (BACKEND / path).read_text(encoding="utf-8")
        if prefix:
            assert re.search(rf"^(?:async )?def {re.escape(prefix)}\w*\(", source, re.M), f"no test {ref}"


def test_every_frontend_and_delegation_reference_exists() -> None:
    doc = FAILURE_MODES_DOC.read_text(encoding="utf-8")
    frontend = FRONTEND_TESTS.read_text(encoding="utf-8")
    for row_id in re.findall(r"`frontend:(FM-[A-Z]+\d+)`", doc):
        assert re.search(rf'test\("[^"]*\b{row_id}\b', frontend), f"no frontend test for {row_id}"
    scenarios = SCENARIOS.read_text(encoding="utf-8")
    for scenario in re.findall(r"`delegation:(\w+)`", doc):
        assert f'"id": "{scenario}"' in scenarios, f"no delegation scenario {scenario}"


def test_failure_tests_and_xfails_name_rows_that_exist() -> None:
    catalogue = rows()
    for path in FAILURE_TEST_FILES:
        source = path.read_text(encoding="utf-8")
        for row in re.findall(r"def test_fm_([a-z]+\d+)_", source):
            assert f"FM-{row.upper()}" in catalogue, f"{path.name}: test for unknown row FM-{row.upper()}"
        for row_id in re.findall(r'reason="(FM-[A-Z]+\d+):', source):
            assert row_id in catalogue, f"{path.name}: xfail for unknown row {row_id}"
            assert "xfail" in catalogue[row_id]["status"], f"{row_id} has a strict xfail but its status is not xfail"
