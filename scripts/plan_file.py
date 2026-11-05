#!/usr/bin/env python3
"""Plan settings (holidays and evolution tracks) from two YAML files.

settings.config_dir() gives the folder. holidays.yaml holds the holidays key
and evolution_tracks.yaml holds the evolution_tracks key. A missing or empty
file gives the default for that file: the default holidays, or no evolution
tracks. A file that is not valid raises PlanConfigError. The message starts
with the file path, then the field, for example
"/config/evolution_tracks.yaml: evolution_tracks[0].preferred_month: must be an
integer from 1 to 12". The older plan.yaml, with both keys, is not read.
load() warns when it is in the folder.
"""

import calendar
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from wine_utils import normalize

DEFAULT_HOLIDAYS: list[tuple[str, int, int]] = [
    ("Thanksgiving", 11, 26),
    ("Christmas", 12, 18),
    ("New Year's Eve", 12, 28),
    ("Valentine's Day", 2, 14),
    ("4th of July", 7, 4),
]

HOLIDAYS_FILE = "holidays.yaml"
TRACKS_FILE = "evolution_tracks.yaml"
# The older single file with both keys. load() only warns that it is not used.
OLD_FILE = "plan.yaml"

_HOLIDAY_KEYS = ("name", "month", "day")
_TRACK_KEYS = ("label", "name_fragment", "season_months", "preferred_month")

# A leap year, so that February 29 is a valid holiday.
_LEAP_YEAR = 2024


class PlanConfigError(ValueError):
    """A plan file is not valid. The message names the file and the field."""


@dataclass(frozen=True)
class PlanConfig:
    # The shapes that the schedulers in generate_plan.py expect.
    holidays: list[tuple[str, int, int]] = field(
        default_factory=lambda: list(DEFAULT_HOLIDAYS)
    )
    evolution_tracks: list[dict] = field(default_factory=list)


def _keys(where: str, value: object, allowed: tuple[str, ...]) -> dict:
    """Check one list item: a mapping with exactly the allowed keys."""
    if not isinstance(value, dict):
        raise PlanConfigError(f"{where}: must be a mapping")
    for key in value:
        if key not in allowed:
            raise PlanConfigError(f"{where}.{key}: unknown field")
    for key in allowed:
        if key not in value:
            raise PlanConfigError(f"{where}.{key}: missing")
    return value


def _text(where: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanConfigError(f"{where}: must be text that is not empty")
    return value


def _fragment(where: str, value: object) -> str:
    # The scheduler matches normalize(fragment) as a substring. An empty result
    # would match every wine.
    text = _text(where, value)
    if not normalize(text):
        raise PlanConfigError(f"{where}: must hold at least one letter or digit")
    return text


def _int_in(where: str, value: object, low: int, high: int, problem: str) -> int:
    # YAML reads "yes" as True, and bool is a subclass of int.
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not low <= value <= high
    ):
        raise PlanConfigError(f"{where}: {problem}")
    return value


def _month(where: str, value: object) -> int:
    return _int_in(where, value, 1, 12, "must be an integer from 1 to 12")


def _holiday(where: str, value: object) -> tuple[str, int, int]:
    item = _keys(where, value, _HOLIDAY_KEYS)
    name = _text(f"{where}.name", item["name"])
    month = _month(f"{where}.month", item["month"])
    last = calendar.monthrange(_LEAP_YEAR, month)[1]
    day = _int_in(
        f"{where}.day",
        item["day"],
        1,
        last,
        f"must be an integer from 1 to {last} for month {month}",
    )
    return (name, month, day)


def _track(where: str, value: object) -> dict:
    item = _keys(where, value, _TRACK_KEYS)
    season = item["season_months"]
    if not isinstance(season, list) or len(season) != 2:
        raise PlanConfigError(
            f"{where}.season_months: must be a list of two months [start, end]"
        )
    start = _month(f"{where}.season_months[0]", season[0])
    end = _month(f"{where}.season_months[1]", season[1])
    # The scheduler uses range(start, end + 1), which is empty for a season
    # that wraps the year end.
    if start > end:
        raise PlanConfigError(
            f"{where}.season_months: the start month must not be after the end month"
        )
    return {
        "label": _text(f"{where}.label", item["label"]),
        "name_fragment": _fragment(f"{where}.name_fragment", item["name_fragment"]),
        "season_months": (start, end),
        "preferred_month": _month(f"{where}.preferred_month", item["preferred_month"]),
    }


def _parse_file(
    data: object, key: str, check: Callable[[str, object], object], default: list
) -> list:
    """Check the data of one file from yaml.safe_load: one optional list key."""
    # An empty file, or the key with no value, gives the default. An empty
    # list is a choice: no items.
    if data is None:
        return default
    if not isinstance(data, dict):
        raise PlanConfigError("top level: must be a mapping")
    for name in data:
        if name != key:
            raise PlanConfigError(f"{name}: unknown field")
    if data.get(key) is None:
        return default
    if not isinstance(data[key], list):
        raise PlanConfigError(f"{key}: must be a list")
    return [check(f"{key}[{i}]", item) for i, item in enumerate(data[key])]


def parse_holidays(data: object) -> list[tuple[str, int, int]]:
    """Check the data of holidays.yaml and return the holidays."""
    return _parse_file(data, "holidays", _holiday, list(DEFAULT_HOLIDAYS))


def parse_tracks(data: object) -> list[dict]:
    """Check the data of evolution_tracks.yaml and return the tracks."""
    return _parse_file(data, "evolution_tracks", _track, [])


def _read(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = f" at line {mark.line + 1}" if mark is not None else ""
        raise PlanConfigError(f"not valid YAML{line}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise PlanConfigError(f"cannot read the file ({exc})") from exc


def _load_file(
    path: Path, parse: Callable[[object], list], missing: str
) -> list | None:
    """Return the checked items of one file, or None when it is missing."""
    if not path.exists():
        print(f"Note: no {path.name} in {path.parent}, {missing}")
        return None
    # One place adds the path, for read errors and field errors alike.
    try:
        items = parse(_read(path))
    except PlanConfigError as exc:
        raise PlanConfigError(f"{path}: {exc}") from exc
    print(f"Loaded {path}")
    return items


def load(config_dir: Path) -> PlanConfig:
    """Read the plan files in config_dir. Raise PlanConfigError for a bad file."""
    old = config_dir / OLD_FILE
    # A host that still has plan.yaml would otherwise lose the holidays and
    # tracks in it with no message.
    if old.exists():
        print(
            f"WARNING: {old} is not used. Move its holidays to {HOLIDAYS_FILE} "
            f"and its evolution_tracks to {TRACKS_FILE} in the same folder.",
            file=sys.stderr,
        )
    holidays = _load_file(
        config_dir / HOLIDAYS_FILE, parse_holidays, "using the default holidays"
    )
    tracks = _load_file(
        config_dir / TRACKS_FILE, parse_tracks, "using no evolution tracks"
    )
    return PlanConfig(
        holidays=list(DEFAULT_HOLIDAYS) if holidays is None else holidays,
        evolution_tracks=[] if tracks is None else tracks,
    )
