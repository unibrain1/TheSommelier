"""Tests for convert_plan_config.py — the one-time plan_config.py to plan.yaml step."""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import generate_plan
import plan_file
import settings
from wine_utils import CURRENT_YEAR

REPO = Path(__file__).resolve().parent.parent
SCRIPT = str(REPO / "scripts" / "convert_plan_config.py")

# A v3 plan_config.py with personal holidays and tracks, and the
# occasion_template field that the generator never read.
OLD_CONFIG = """\
HOLIDAYS: list[tuple[str, int, int]] = [
    ("Thanksgiving", 11, 26),
    ("Christmas", 12, 18),
    ("New Year's Eve", 12, 28),
    ("Valentine's Day", 2, 14),
    ("4th of July", 7, 4),
    ("Anniversary", 5, 28),
    ("Birthday", 10, 10),
]

EVOLUTION_TRACKS: list[dict] = [
    {
        "label": "Laurène",
        "name_fragment": "laurène",
        "season_months": (10, 11),
        "preferred_month": 11,
        "occasion_template": "{occasion} — Laurène evolution",
    },
    {
        "label": "Louise",
        "name_fragment": "louise",
        "season_months": (12, 12),
        "preferred_month": 12,
        "occasion_template": "{occasion} — Louise evolution",
    },
]
"""


def wine(i: int, name: str, vintage: int, qty: int, end: int, score: float) -> dict:
    return {
        "iWine": str(i), "Wine": name, "Vintage": vintage, "Quantity": qty,
        "BeginConsume": CURRENT_YEAR - 3, "EndConsume": end, "Type": "Red",
        "Varietal": "Pinot Noir", "Producer": "P", "Location": "Cellar",
        "Bin": "A1", "CT": score,
    }  # fmt: skip


def inventory() -> list[dict]:
    return [
        wine(1, "Domaine Drouhin Laurène Pinot Noir", 2016, 3, CURRENT_YEAR + 4, 93.0),
        wine(2, "Domaine Drouhin Laurène Pinot Noir", 2018, 3, CURRENT_YEAR + 6, 94.0),
        wine(3, "Domaine Drouhin Louise Pinot Noir", 2017, 3, CURRENT_YEAR + 5, 95.0),
        wine(4, "Everyday Pinot", 2020, 60, CURRENT_YEAR + 3, 89.0),
        wine(5, "Weeknight Red", 2021, 60, CURRENT_YEAR + 2, 88.0),
        # Holiday anchors skip each wine that is already reserved, so the
        # plan needs more labels than holidays and tracks.
        *(
            wine(10 + n, f"Cellar Red {n}", 2019, 10, CURRENT_YEAR + 3, 90.0)
            for n in range(10)
        ),
    ]


@pytest.fixture
def old_config(tmp_path) -> Path:
    path = tmp_path / "plan_config.py"
    path.write_text(OLD_CONFIG, encoding="utf-8")
    return path


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, SCRIPT, *args], capture_output=True, text=True
    )


def old_module(path: Path):
    namespace: dict = {}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
    return namespace


def test_converted_file_has_the_same_values(old_config, tmp_path):
    target = tmp_path / "out" / "plan.yaml"
    result = run(str(old_config), str(target))
    assert result.returncode == 0, result.stderr

    old = old_module(old_config)
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert [(h["name"], h["month"], h["day"]) for h in data["holidays"]] == old[
        "HOLIDAYS"
    ]
    assert len(data["evolution_tracks"]) == 2
    assert all("occasion_template" not in t for t in data["evolution_tracks"])
    assert "Laurène" in target.read_text(encoding="utf-8")


def test_converted_file_gives_the_same_plan(old_config, tmp_path, monkeypatch):
    for name in settings.ALL_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    target = tmp_path / "plan.yaml"
    assert run(str(old_config), str(target)).returncode == 0

    old = old_module(old_config)
    old_plan = generate_plan.generate_plan(
        inventory(),
        plan_file.PlanConfig(
            holidays=old["HOLIDAYS"], evolution_tracks=old["EVOLUTION_TRACKS"]
        ),
    )
    new_plan = generate_plan.generate_plan(inventory(), plan_file.load(target))

    assert new_plan["allWeeks"] == old_plan["allWeeks"]
    # The check means something only when the tracks and holidays are used.
    occasions = [w["occasion"] or "" for w in new_plan["allWeeks"]]
    assert any(w["evolution"] for w in new_plan["allWeeks"])
    assert any("Anniversary" in o or "Birthday" in o for o in occasions)


def test_existing_target_is_not_overwritten(old_config, tmp_path):
    target = tmp_path / "plan.yaml"
    target.write_text("keep me\n", encoding="utf-8")
    result = run(str(old_config), str(target))
    assert result.returncode == 1
    assert "exists" in result.stderr
    assert target.read_text(encoding="utf-8") == "keep me\n"


def test_missing_source_is_an_error(tmp_path):
    result = run(str(tmp_path / "nope.py"), str(tmp_path / "plan.yaml"))
    assert result.returncode == 1
    assert "not found" in result.stderr
    assert not (tmp_path / "plan.yaml").exists()


def test_source_that_fails_the_check_writes_nothing(tmp_path):
    source = tmp_path / "plan_config.py"
    source.write_text('HOLIDAYS = [("Bad", 13, 1)]\n', encoding="utf-8")
    result = run(str(source), str(tmp_path / "plan.yaml"))
    assert result.returncode == 1
    assert "holidays[0].month" in result.stderr
    assert not (tmp_path / "plan.yaml").exists()


def test_wrong_argument_count_is_usage_error():
    result = run("only-one")
    assert result.returncode == 2
    assert "Usage" in result.stderr
