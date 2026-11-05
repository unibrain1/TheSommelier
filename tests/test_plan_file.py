"""Tests for plan_file.py (holidays.yaml and evolution_tracks.yaml) and generate_plan."""

import ast
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

import generate_plan
import plan_file
import settings

REPO = Path(__file__).resolve().parent.parent
HOLIDAYS_SAMPLE = REPO / "config" / "holidays.yaml.sample"
TRACKS_SAMPLE = REPO / "config" / "evolution_tracks.yaml.sample"

HOLIDAYS_TEXT = """\
holidays:
  - name: Anniversary
    month: 5
    day: 28
  - name: Leap Day
    month: 2
    day: 29
"""
TRACKS_TEXT = """\
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

# A literal list, so the test does not compare the constant with itself.
LITERAL_DEFAULTS = [
    ("Thanksgiving", 11, 26),
    ("Christmas", 12, 18),
    ("New Year's Eve", 12, 28),
    ("Valentine's Day", 2, 14),
    ("4th of July", 7, 4),
]


# Enough bottles for 52 weeks.
WINE = {
    "iWine": "1", "Wine": "Test Pinot", "Vintage": 2018, "Quantity": 60,
    "BeginConsume": 2020, "EndConsume": 2040, "Type": "Red",
    "Varietal": "Pinot Noir", "Producer": "P", "Location": "Cellar",
    "Bin": "A1", "CT": 90.0,
}  # fmt: skip


def write(
    tmp_path: Path, holidays: str | None = None, tracks: str | None = None
) -> Path:
    """Write the given files into tmp_path (None writes no file). Return it."""
    if holidays is not None:
        (tmp_path / plan_file.HOLIDAYS_FILE).write_text(holidays, encoding="utf-8")
    if tracks is not None:
        (tmp_path / plan_file.TRACKS_FILE).write_text(tracks, encoding="utf-8")
    return tmp_path


def file_of(text: str) -> str:
    if text.startswith("evolution_tracks"):
        return plan_file.TRACKS_FILE
    return plan_file.HOLIDAYS_FILE


def write_for(tmp_path: Path, text: str) -> Path:
    """Write text to the file that its top-level key belongs to."""
    (tmp_path / file_of(text)).write_text(text, encoding="utf-8")
    return tmp_path


# --- missing and valid files ----------------------------------------------


def test_no_files_gives_default_holidays_and_no_tracks(tmp_path, capsys):
    config = plan_file.load(tmp_path)
    assert config.holidays == LITERAL_DEFAULTS
    assert config.evolution_tracks == []


def test_holidays_file_alone_keeps_no_tracks(tmp_path):
    config = plan_file.load(write(tmp_path, holidays=HOLIDAYS_TEXT))
    assert config.holidays == [("Anniversary", 5, 28), ("Leap Day", 2, 29)]
    assert config.evolution_tracks == []


def test_tracks_file_alone_keeps_default_holidays(tmp_path):
    config = plan_file.load(write(tmp_path, tracks=TRACKS_TEXT))
    assert config.holidays == LITERAL_DEFAULTS
    assert len(config.evolution_tracks) == 1


def test_default_holidays_are_the_five_public_ones():
    assert plan_file.DEFAULT_HOLIDAYS == LITERAL_DEFAULTS


def test_valid_files_load_with_scheduler_shapes(tmp_path):
    config = plan_file.load(write(tmp_path, HOLIDAYS_TEXT, TRACKS_TEXT))
    assert config.holidays == [("Anniversary", 5, 28), ("Leap Day", 2, 29)]
    assert config.evolution_tracks == [
        {
            "label": "Laurène",
            "name_fragment": "laurène",
            "season_months": (10, 11),
            "preferred_month": 11,
        }
    ]


@pytest.mark.parametrize(
    "file,attr,text,expected",
    [
        (plan_file.HOLIDAYS_FILE, "holidays", "", LITERAL_DEFAULTS),
        (plan_file.HOLIDAYS_FILE, "holidays", "# only a comment\n", LITERAL_DEFAULTS),
        (plan_file.HOLIDAYS_FILE, "holidays", "holidays:\n", LITERAL_DEFAULTS),
        (plan_file.TRACKS_FILE, "evolution_tracks", "", []),
        (plan_file.TRACKS_FILE, "evolution_tracks", "# only a comment\n", []),
        (plan_file.TRACKS_FILE, "evolution_tracks", "evolution_tracks:\n", []),
    ],
)
def test_empty_file_gives_the_file_default(tmp_path, file, attr, text, expected):
    (tmp_path / file).write_text(text, encoding="utf-8")
    assert getattr(plan_file.load(tmp_path), attr) == expected


def test_empty_holiday_list_gives_no_holidays(tmp_path):
    config = plan_file.load(write(tmp_path, holidays="holidays: []\n"))
    assert config.holidays == []


def test_empty_tracks_list_gives_no_tracks(tmp_path):
    config = plan_file.load(write(tmp_path, tracks="evolution_tracks: []\n"))
    assert config.evolution_tracks == []


def test_defaults_are_a_copy(tmp_path):
    config = plan_file.load(tmp_path)
    config.holidays.append(("Extra", 1, 1))
    assert ("Extra", 1, 1) not in plan_file.DEFAULT_HOLIDAYS
    assert plan_file.parse_holidays(None) is not plan_file.DEFAULT_HOLIDAYS


# --- log lines --------------------------------------------------------------


@pytest.mark.parametrize("has_holidays", [False, True])
@pytest.mark.parametrize("has_tracks", [False, True])
def test_log_line_per_file(tmp_path, capsys, has_holidays, has_tracks):
    write(
        tmp_path,
        HOLIDAYS_TEXT if has_holidays else None,
        TRACKS_TEXT if has_tracks else None,
    )
    plan_file.load(tmp_path)
    out = capsys.readouterr().out
    h, t = tmp_path / "holidays.yaml", tmp_path / "evolution_tracks.yaml"
    if has_holidays:
        assert f"Loaded {h}" in out
        assert "default holidays" not in out
    else:
        assert (
            f"Note: no holidays.yaml in {tmp_path}, using the default holidays" in out
        )
        assert f"Loaded {h}" not in out
    if has_tracks:
        assert f"Loaded {t}" in out
        assert "using no evolution tracks" not in out
    else:
        assert (
            f"Note: no evolution_tracks.yaml in {tmp_path}, using no evolution tracks"
            in out
        )
        assert f"Loaded {t}" not in out


# --- the old plan.yaml ------------------------------------------------------


def test_old_plan_yaml_warns_and_is_ignored(tmp_path, capsys):
    old = tmp_path / "plan.yaml"
    old.write_text(HOLIDAY.replace("name: X", "name: OldFileDay"), encoding="utf-8")
    config = plan_file.load(tmp_path)
    captured = capsys.readouterr()
    for word in ("plan.yaml", "holidays.yaml", "evolution_tracks.yaml"):
        assert word in captured.err
    assert "WARNING" in captured.err
    assert "OldFileDay" not in [h[0] for h in config.holidays]
    assert config.holidays == LITERAL_DEFAULTS


def test_no_old_file_gives_no_warning(tmp_path, capsys):
    plan_file.load(tmp_path)
    assert capsys.readouterr().err == ""


def test_old_plan_yaml_is_not_read_even_when_invalid(tmp_path):
    (tmp_path / "plan.yaml").write_text("not: [valid\n", encoding="utf-8")
    assert plan_file.load(tmp_path).holidays == LITERAL_DEFAULTS


# --- the samples (no personal data) ----------------------------------------


def test_holidays_sample_loads_as_default_holidays(tmp_path):
    config = plan_file.load(write(tmp_path, HOLIDAYS_SAMPLE.read_text("utf-8")))
    assert config.holidays == LITERAL_DEFAULTS
    assert config.evolution_tracks == []


def test_tracks_sample_loads_as_no_tracks(tmp_path):
    config = plan_file.load(write(tmp_path, tracks=TRACKS_SAMPLE.read_text("utf-8")))
    assert config.evolution_tracks == []
    assert config.holidays == LITERAL_DEFAULTS


def _uncomment(text: str, marker: str) -> str:
    """Return the commented block that starts at marker, with "# " removed."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if marker in line)
    block = []
    for line in lines[start:]:
        if not line.lstrip().startswith("#"):
            break
        block.append(re.sub(r"^(\s*)# ?", r"\1", line))
    return "\n".join(block) + "\n"


def test_sample_examples_are_generic_and_valid():
    holidays = plan_file.parse_holidays(
        yaml.safe_load(
            "holidays:\n"
            + _uncomment(HOLIDAYS_SAMPLE.read_text("utf-8"), "- name: Birthday")
        )
    )
    assert [h[0] for h in holidays] == ["Birthday"]

    tracks_text = TRACKS_SAMPLE.read_text("utf-8")
    block = _uncomment(tracks_text, "# evolution_tracks:")
    tracks = plan_file.parse_tracks(yaml.safe_load(block))
    assert [t["label"] for t in tracks] == ["Example Pinot Noir"]

    for sample in (HOLIDAYS_SAMPLE, TRACKS_SAMPLE):
        assert "laurène" not in sample.read_text("utf-8").lower()


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
def test_invalid_file_names_the_file_and_the_field(tmp_path, text, field):
    folder = write_for(tmp_path, text)
    with pytest.raises(plan_file.PlanConfigError) as excinfo:
        plan_file.load(folder)
    assert str(excinfo.value).startswith(f"{folder / file_of(text)}: {field}")


@pytest.mark.parametrize("file", [plan_file.HOLIDAYS_FILE, plan_file.TRACKS_FILE])
@pytest.mark.parametrize("text", ["- a\n- b\n", "hello\n"])
def test_top_level_not_a_mapping_names_the_file(tmp_path, file, text):
    (tmp_path / file).write_text(text, encoding="utf-8")
    with pytest.raises(plan_file.PlanConfigError) as excinfo:
        plan_file.load(tmp_path)
    assert str(excinfo.value).startswith(f"{tmp_path / file}: top level: must be")


@pytest.mark.parametrize(
    "file,text,field",
    [
        (plan_file.HOLIDAYS_FILE, "holidays: 1\n", "holidays: must be a list"),
        (plan_file.HOLIDAYS_FILE, "holidays: {}\n", "holidays: must be a list"),
        (
            plan_file.TRACKS_FILE,
            "evolution_tracks: text\n",
            "evolution_tracks: must be a list",
        ),
    ],
)
def test_top_key_wrong_type_names_the_file(tmp_path, file, text, field):
    (tmp_path / file).write_text(text, encoding="utf-8")
    with pytest.raises(plan_file.PlanConfigError) as excinfo:
        plan_file.load(tmp_path)
    assert str(excinfo.value) == f"{tmp_path / file}: {field}"


def test_holidays_key_in_tracks_file_is_unknown_field(tmp_path):
    folder = write(tmp_path, tracks=HOLIDAY)
    with pytest.raises(plan_file.PlanConfigError) as excinfo:
        plan_file.load(folder)
    assert str(excinfo.value) == (
        f"{folder / plan_file.TRACKS_FILE}: holidays: unknown field"
    )


def test_tracks_key_in_holidays_file_is_unknown_field(tmp_path):
    folder = write(tmp_path, holidays=TRACK)
    with pytest.raises(plan_file.PlanConfigError) as excinfo:
        plan_file.load(folder)
    assert str(excinfo.value) == (
        f"{folder / plan_file.HOLIDAYS_FILE}: evolution_tracks: unknown field"
    )


def test_both_keys_in_one_file_is_unknown_field(tmp_path):
    folder = write(tmp_path, holidays=HOLIDAY + TRACK)
    with pytest.raises(plan_file.PlanConfigError, match="evolution_tracks: unknown"):
        plan_file.load(folder)


def test_parse_functions_take_data_directly():
    assert plan_file.parse_holidays(None) == LITERAL_DEFAULTS
    assert plan_file.parse_tracks(None) == []
    with pytest.raises(plan_file.PlanConfigError, match=r"^holidays\[0\]\.day:"):
        plan_file.parse_holidays({"holidays": [{"name": "X", "month": 2, "day": 30}]})


def test_second_item_error_names_its_index(tmp_path):
    text = HOLIDAY + "  - name: Y\n    month: 99\n    day: 1\n"
    with pytest.raises(
        plan_file.PlanConfigError, match=r"holidays\.yaml: holidays\[1\]\.month:"
    ):
        plan_file.load(write(tmp_path, holidays=text))


@pytest.mark.parametrize(
    "file,text",
    [
        (plan_file.HOLIDAYS_FILE, "holidays:\n  - name: X\n    month: [5\n"),
        (plan_file.TRACKS_FILE, "evolution_tracks:\n  - label: [x\n"),
    ],
)
def test_yaml_syntax_error_names_the_file_and_line(tmp_path, file, text):
    (tmp_path / file).write_text(text, encoding="utf-8")
    with pytest.raises(plan_file.PlanConfigError) as excinfo:
        plan_file.load(tmp_path)
    assert re.match(
        rf"{re.escape(str(tmp_path / file))}: not valid YAML at line \d+",
        str(excinfo.value),
    )


@pytest.mark.parametrize(
    "file,key",
    [
        (plan_file.HOLIDAYS_FILE, "holidays"),
        (plan_file.TRACKS_FILE, "evolution_tracks"),
    ],
)
def test_python_tags_are_not_loaded(tmp_path, file, key):
    # safe_load rejects Python object tags, so a file cannot run code.
    (tmp_path / file).write_text(
        f"{key}: !!python/object/apply:os.system ['true']\n", encoding="utf-8"
    )
    with pytest.raises(plan_file.PlanConfigError, match="not valid YAML"):
        plan_file.load(tmp_path)


@pytest.mark.parametrize("file", [plan_file.HOLIDAYS_FILE, plan_file.TRACKS_FILE])
def test_unreadable_path_is_a_plan_config_error(tmp_path, file):
    (tmp_path / file).mkdir()
    with pytest.raises(plan_file.PlanConfigError, match="cannot read the file") as e:
        plan_file.load(tmp_path)
    assert str(tmp_path / file) in str(e.value)


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
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SITE_DIR", str(site))
    monkeypatch.setenv("CONFIG_DIR", str(config))
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps([WINE]), encoding="utf-8")
    out = tmp_path / "plan.json"
    monkeypatch.setattr(sys, "argv", ["generate_plan.py", str(inventory), str(out)])

    def run(holidays: str | None = None, tracks: str | None = None):
        write(config, holidays, tracks)
        generate_plan.main()
        return out, data, site

    return run


@pytest.mark.parametrize(
    "holidays,tracks,field,file",
    [
        (None, TRACK.replace("preferred_month", "prefered_month"),
         "evolution_tracks[0].prefered_month: unknown field", "evolution_tracks.yaml"),
        ("holidays: 1\n", None, "holidays: must be a list", "holidays.yaml"),
    ],
)  # fmt: skip
def test_main_with_invalid_file_exits_1_and_names_file_and_field(
    run_main, capsys, tmp_path, holidays, tracks, field, file
):
    with pytest.raises(SystemExit) as excinfo:
        run_main(holidays, tracks)
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert f"Error: {tmp_path / 'config' / file}: {field}" in err


def test_main_with_invalid_file_writes_nothing(run_main, tmp_path):
    with pytest.raises(SystemExit):
        run_main("holidays: 1\n")
    assert not (tmp_path / "plan.json").exists()
    assert not (tmp_path / "data" / "plan_previous.json").exists()


def test_main_without_files_uses_defaults_and_logs(run_main, capsys):
    out, _, _ = run_main()
    assert "default holidays" in capsys.readouterr().out
    weeks = json.loads(out.read_text(encoding="utf-8"))["allWeeks"]
    assert len(weeks) == 52
    # The plan has one label, so only the first default holiday gets an anchor.
    defaults = {h[0] for h in LITERAL_DEFAULTS}
    assert any(w["occasion"] in defaults for w in weeks)


def test_custom_birthday_in_holidays_file_is_in_the_plan(run_main):
    birthday = "holidays:\n  - name: Birthday\n    month: 1\n    day: 15\n"
    out, _, _ = run_main(holidays=birthday)
    weeks = json.loads(out.read_text(encoding="utf-8"))["allWeeks"]
    assert any(w["occasion"] and "Birthday" in w["occasion"] for w in weeks)
