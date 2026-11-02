"""Tests for plan_file.py — the plan.yaml loader — and its use in generate_plan."""

import ast
import json
import sys
from pathlib import Path

import pytest

import generate_plan
import plan_file
import settings

REPO = Path(__file__).resolve().parent.parent
SAMPLE = REPO / "config" / "plan.yaml.sample"

VALID = """\
holidays:
  - name: Anniversary
    month: 5
    day: 28
  - name: Leap Day
    month: 2
    day: 29
evolution_tracks:
  - label: Laurène
    name_fragment: laurène
    season_months: [10, 11]
    preferred_month: 11
"""

HOLIDAY = "holidays:\n  - name: X\n    month: 5\n    day: 28\n"
TRACK = (
    "evolution_tracks:\n  - label: L\n    name_fragment: l\n"
    "    season_months: [10, 11]\n    preferred_month: 11\n"
)


# Enough bottles for 52 weeks.
WINE = {
    "iWine": "1", "Wine": "Test Pinot", "Vintage": 2018, "Quantity": 60,
    "BeginConsume": 2020, "EndConsume": 2040, "Type": "Red",
    "Varietal": "Pinot Noir", "Producer": "P", "Location": "Cellar",
    "Bin": "A1", "CT": 90.0,
}  # fmt: skip


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "plan.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# --- missing and valid files ----------------------------------------------


def test_missing_file_gives_defaults_and_logs(tmp_path, capsys):
    config = plan_file.load(tmp_path / "nope.yaml")
    assert config.holidays == plan_file.DEFAULT_HOLIDAYS
    assert config.evolution_tracks == []
    out = capsys.readouterr().out
    assert "default holidays" in out
    assert str(tmp_path / "nope.yaml") in out


def test_default_holidays_are_the_five_of_the_sample():
    assert [h[0] for h in plan_file.DEFAULT_HOLIDAYS] == [
        "Thanksgiving",
        "Christmas",
        "New Year's Eve",
        "Valentine's Day",
        "4th of July",
    ]


def test_valid_file_loads_with_scheduler_shapes(tmp_path):
    config = plan_file.load(write(tmp_path, VALID))
    assert config.holidays == [("Anniversary", 5, 28), ("Leap Day", 2, 29)]
    assert config.evolution_tracks == [
        {
            "label": "Laurène",
            "name_fragment": "laurène",
            "season_months": (10, 11),
            "preferred_month": 11,
        }
    ]


def test_sample_file_loads_as_the_defaults():
    config = plan_file.load(SAMPLE)
    assert config.holidays == plan_file.DEFAULT_HOLIDAYS
    assert config.evolution_tracks == []


@pytest.mark.parametrize("text", ["", "# only a comment\n"])
def test_empty_file_gives_defaults(tmp_path, text):
    config = plan_file.load(write(tmp_path, text))
    assert config.holidays == plan_file.DEFAULT_HOLIDAYS
    assert config.evolution_tracks == []


def test_tracks_only_keeps_default_holidays(tmp_path):
    config = plan_file.load(write(tmp_path, TRACK))
    assert config.holidays == plan_file.DEFAULT_HOLIDAYS
    assert len(config.evolution_tracks) == 1


def test_empty_holiday_list_gives_no_holidays(tmp_path):
    config = plan_file.load(write(tmp_path, "holidays: []\n"))
    assert config.holidays == []


def test_defaults_are_a_copy(tmp_path):
    config = plan_file.load(tmp_path / "nope.yaml")
    config.holidays.append(("Extra", 1, 1))
    assert ("Extra", 1, 1) not in plan_file.DEFAULT_HOLIDAYS


# --- invalid files: each error names the field ---------------------------

INVALID = [
    ("unknown top-level key", "holiday:\n  - name: X\n", "holiday: unknown field"),
    (
        "unknown holiday key",
        HOLIDAY + "    date: 1\n",
        "holidays[0].date: unknown field",
    ),
    (
        "unknown track key (typo)",
        TRACK.replace("preferred_month", "prefered_month"),
        "evolution_tracks[0].prefered_month: unknown field",
    ),
    (
        "missing name",
        "holidays:\n  - month: 5\n    day: 28\n",
        "holidays[0].name: missing",
    ),
    (
        "missing season_months",
        "evolution_tracks:\n  - label: L\n    name_fragment: l\n"
        "    preferred_month: 11\n",
        "evolution_tracks[0].season_months: missing",
    ),
    ("month 13", HOLIDAY.replace("month: 5", "month: 13"), "holidays[0].month:"),
    ("month 0", HOLIDAY.replace("month: 5", "month: 0"), "holidays[0].month:"),
    ("month as text", HOLIDAY.replace("month: 5", 'month: "5"'), "holidays[0].month:"),
    ("month as bool", HOLIDAY.replace("month: 5", "month: yes"), "holidays[0].month:"),
    ("month as float", HOLIDAY.replace("month: 5", "month: 5.0"), "holidays[0].month:"),
    (
        "April 31",
        HOLIDAY.replace("month: 5", "month: 4").replace("28", "31"),
        "holidays[0].day:",
    ),
    (
        "February 30",
        HOLIDAY.replace("month: 5", "month: 2").replace("28", "30"),
        "holidays[0].day:",
    ),
    ("day as text", HOLIDAY.replace("day: 28", "day: twenty"), "holidays[0].day:"),
    ("day as bool", HOLIDAY.replace("day: 28", "day: true"), "holidays[0].day:"),
    ("empty name", HOLIDAY.replace("name: X", 'name: ""'), "holidays[0].name:"),
    ("name as number", HOLIDAY.replace("name: X", "name: 42"), "holidays[0].name:"),
    (
        "empty name_fragment",
        TRACK.replace("name_fragment: l", "name_fragment: ' '"),
        "evolution_tracks[0].name_fragment:",
    ),
    (
        "label as list",
        TRACK.replace("label: L", "label: [a]"),
        "evolution_tracks[0].label:",
    ),
    (
        "one season month",
        TRACK.replace("[10, 11]", "[11]"),
        "evolution_tracks[0].season_months:",
    ),
    (
        "season months as text",
        TRACK.replace("[10, 11]", "'10-11'"),
        "evolution_tracks[0].season_months:",
    ),
    (
        "season month 13",
        TRACK.replace("[10, 11]", "[10, 13]"),
        "evolution_tracks[0].season_months[1]:",
    ),
    (
        "season wraps the year end",
        TRACK.replace("[10, 11]", "[12, 2]"),
        "evolution_tracks[0].season_months:",
    ),
    (
        "preferred_month as text",
        TRACK.replace("preferred_month: 11", "preferred_month: November"),
        "evolution_tracks[0].preferred_month:",
    ),
    ("holidays not a list", "holidays: Christmas\n", "holidays: must be a list"),
    (
        "evolution_tracks not a list",
        "evolution_tracks: {}\n",
        "evolution_tracks: must be a list",
    ),
    (
        "holiday not a mapping",
        "holidays:\n  - Christmas\n",
        "holidays[0]: must be a mapping",
    ),
    ("top level a list", "- a\n- b\n", "top level: must be a mapping"),
    ("top level text", "hello\n", "top level: must be a mapping"),
    ("null name", HOLIDAY.replace("name: X", "name:"), "holidays[0].name:"),
    ("null month", HOLIDAY.replace("month: 5", "month:"), "holidays[0].month:"),
    ("day 0", HOLIDAY.replace("day: 28", "day: 0"), "holidays[0].day:"),
    ("day as float", HOLIDAY.replace("day: 28", "day: 28.0"), "holidays[0].day:"),
    ("null holiday item", "holidays:\n  -\n", "holidays[0]: must be a mapping"),
    (
        "label as number",
        TRACK.replace("label: L", "label: 7"),
        "evolution_tracks[0].label:",
    ),
    (
        "name_fragment with no letter or digit",
        TRACK.replace("name_fragment: l", "name_fragment: '!!!'"),
        "evolution_tracks[0].name_fragment:",
    ),
    (
        "season month as text",
        TRACK.replace("[10, 11]", "['10', 11]"),
        "evolution_tracks[0].season_months[0]:",
    ),
    (
        "season month as bool",
        TRACK.replace("[10, 11]", "[true, 11]"),
        "evolution_tracks[0].season_months[0]:",
    ),
    (
        "season month 0",
        TRACK.replace("[10, 11]", "[0, 11]"),
        "evolution_tracks[0].season_months[0]:",
    ),
    (
        "three season months",
        TRACK.replace("[10, 11]", "[10, 11, 12]"),
        "evolution_tracks[0].season_months:",
    ),
    (
        "null season_months",
        TRACK.replace("[10, 11]", ""),
        "evolution_tracks[0].season_months:",
    ),
    *(
        (
            f"preferred_month {bad}",
            TRACK.replace("preferred_month: 11", f"preferred_month: {bad}"),
            "evolution_tracks[0].preferred_month:",
        )
        for bad in ("yes", "0", "13", "11.0")
    ),
]


@pytest.mark.parametrize(
    "text,field", [(t, f) for _, t, f in INVALID], ids=[c for c, _, _ in INVALID]
)
def test_invalid_file_names_the_field(tmp_path, text, field):
    with pytest.raises(plan_file.PlanConfigError) as excinfo:
        plan_file.load(write(tmp_path, text))
    assert str(excinfo.value).startswith(field)


def test_second_item_error_names_its_index(tmp_path):
    text = HOLIDAY + "  - name: Y\n    month: 99\n    day: 1\n"
    with pytest.raises(plan_file.PlanConfigError, match=r"^holidays\[1\]\.month:"):
        plan_file.load(write(tmp_path, text))


def test_yaml_syntax_error_names_the_line(tmp_path):
    text = "holidays:\n  - name: X\n    month: [5\n"
    with pytest.raises(plan_file.PlanConfigError, match=r"not valid YAML at line \d+"):
        plan_file.load(write(tmp_path, text))


def test_python_tags_are_not_loaded(tmp_path):
    # safe_load rejects Python object tags, so a file cannot run code.
    text = "holidays: !!python/object/apply:os.system ['true']\n"
    with pytest.raises(plan_file.PlanConfigError, match="not valid YAML"):
        plan_file.load(write(tmp_path, text))


def test_unreadable_path_is_a_plan_config_error(tmp_path):
    folder = tmp_path / "plan.yaml"
    folder.mkdir()
    with pytest.raises(plan_file.PlanConfigError, match="cannot read the file"):
        plan_file.load(folder)


# --- generate_plan ---------------------------------------------------------


def test_generate_plan_does_not_import_plan_config():
    source = (REPO / "scripts" / "generate_plan.py").read_text(encoding="utf-8")
    modules = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    assert "plan_config" not in modules
    assert "plan_file" in modules


@pytest.fixture
def run_main(monkeypatch, tmp_path):
    for name in settings.ALL_VARS:
        monkeypatch.delenv(name, raising=False)
    data, site = tmp_path / "data", tmp_path / "site"
    data.mkdir()
    site.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SITE_DIR", str(site))
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps([WINE]), encoding="utf-8")
    out = tmp_path / "plan.json"
    monkeypatch.setattr(sys, "argv", ["generate_plan.py", str(inventory), str(out)])

    def run(plan_text: str | None):
        path = tmp_path / "plan.yaml"
        if plan_text is not None:
            path.write_text(plan_text, encoding="utf-8")
        monkeypatch.setenv("PLAN_CONFIG", str(path))
        generate_plan.main()
        return out, data, site

    return run


def test_main_with_invalid_file_exits_1_and_names_the_field(run_main, capsys):
    with pytest.raises(SystemExit) as excinfo:
        run_main(TRACK.replace("preferred_month", "prefered_month"))
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "evolution_tracks[0].prefered_month: unknown field" in err
    assert "plan.yaml" in err


def test_main_with_invalid_file_writes_nothing(run_main, tmp_path):
    with pytest.raises(SystemExit):
        run_main("holidays: 1\n")
    assert not (tmp_path / "plan.json").exists()
    assert not (tmp_path / "data" / "plan_previous.json").exists()


def test_main_without_file_uses_defaults_and_logs(run_main, capsys):
    out, _, _ = run_main(None)
    assert "default holidays" in capsys.readouterr().out
    weeks = json.loads(out.read_text(encoding="utf-8"))["allWeeks"]
    assert len(weeks) == 52
    # The plan has one label, so only the first default holiday gets an anchor.
    defaults = {h[0] for h in plan_file.DEFAULT_HOLIDAYS}
    assert any(w["occasion"] in defaults for w in weeks)
