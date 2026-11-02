#!/usr/bin/env python3
"""Plan settings (holidays and evolution tracks) from a YAML file.

settings.plan_config_path() gives the path. A missing file gives the default
holidays and no evolution tracks. A file that is not valid raises
PlanConfigError. For a bad value the message starts with the field, for
example "evolution_tracks[0].preferred_month: must be an integer from 1 to 12".
"""

import calendar
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

_HOLIDAY_KEYS = ("name", "month", "day")
_TRACK_KEYS = ("label", "name_fragment", "season_months", "preferred_month")
_TOP_KEYS = ("holidays", "evolution_tracks")

# A leap year, so that February 29 is a valid holiday.
_LEAP_YEAR = 2024


class PlanConfigError(ValueError):
    """The plan file is not valid. The message names the field."""


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


def _items(data: dict, key: str, check: Callable[[str, object], object]) -> list | None:
    """Check each item of an optional top-level list. Return None if absent."""
    if key not in data:
        return None
    if not isinstance(data[key], list):
        raise PlanConfigError(f"{key}: must be a list")
    return [check(f"{key}[{i}]", item) for i, item in enumerate(data[key])]


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


def parse(data: object) -> PlanConfig:
    """Check the data from yaml.safe_load and return the plan settings."""
    if data is None:
        return PlanConfig()
    # Both top-level keys are optional, so this check is not _keys().
    if not isinstance(data, dict):
        raise PlanConfigError("top level: must be a mapping")
    for key in data:
        if key not in _TOP_KEYS:
            raise PlanConfigError(f"{key}: unknown field")
    holidays = _items(data, "holidays", _holiday)
    tracks = _items(data, "evolution_tracks", _track)
    return PlanConfig(
        holidays=list(DEFAULT_HOLIDAYS) if holidays is None else holidays,
        evolution_tracks=tracks or [],
    )


def load(path: Path) -> PlanConfig:
    """Read the plan file. Raise PlanConfigError when it is not valid."""
    if not path.exists():
        print(
            f"Note: no plan file at {path}, "
            "using the default holidays and no evolution tracks"
        )
        return PlanConfig()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = f" at line {mark.line + 1}" if mark is not None else ""
        raise PlanConfigError(f"not valid YAML{line}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise PlanConfigError(f"cannot read the file ({exc})") from exc
    return parse(data)
