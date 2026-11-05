#!/usr/bin/env python3
"""Convert a v3 plan_config.py to the plan files. Run it one time, by hand.

Usage:
    python3 scripts/convert_plan_config.py scripts/plan_config.py config

The script writes holidays.yaml when the Python file has HOLIDAYS, and
evolution_tracks.yaml when it has EVOLUTION_TRACKS, in the given folder. It
imports the Python file, so give it only your own plan_config.py. It drops
occasion_template, because the plan generator never read it. It checks each
result with the matching plan_file parse function, so the output always
loads. If a file that it would write exists, it writes no file. If a write
fails, it removes the files that it wrote. A Python file with neither
setting is an error.
"""

import importlib.util
import sys
from pathlib import Path

import yaml

import plan_file


def convert(source: Path) -> dict[str, dict]:
    """Return the data of each plan file for one plan_config.py, by file name."""
    spec = importlib.util.spec_from_file_location("plan_config_to_convert", source)
    if spec is None or spec.loader is None:
        raise plan_file.PlanConfigError(f"cannot load {source} as Python")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    files: dict[str, dict] = {}
    if hasattr(module, "HOLIDAYS"):
        holidays = {
            "holidays": [
                {"name": name, "month": month, "day": day}
                for name, month, day in module.HOLIDAYS
            ]
        }
        plan_file.parse_holidays(holidays)
        files[plan_file.HOLIDAYS_FILE] = holidays
    if hasattr(module, "EVOLUTION_TRACKS"):
        tracks = {
            "evolution_tracks": [
                {
                    "label": track["label"],
                    "name_fragment": track["name_fragment"],
                    "season_months": list(track["season_months"]),
                    "preferred_month": track["preferred_month"],
                }
                for track in module.EVOLUTION_TRACKS
            ]
        }
        plan_file.parse_tracks(tracks)
        files[plan_file.TRACKS_FILE] = tracks
    return files


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "Usage: convert_plan_config.py <plan_config.py> <config_dir>",
            file=sys.stderr,
        )
        return 2
    source, target_dir = Path(argv[0]), Path(argv[1])
    if not source.is_file():
        print(f"Error: file not found: {source}", file=sys.stderr)
        return 1
    try:
        files = convert(source)
    # PlanConfigError is a ValueError.
    except (KeyError, TypeError, ValueError) as exc:
        print(f"Error: {source}: {exc}", file=sys.stderr)
        return 1
    if not files:
        print(
            f"Error: {source} has no HOLIDAYS and no EVOLUTION_TRACKS.",
            file=sys.stderr,
        )
        return 1
    # Check all targets before the first write, so a stop leaves the folder
    # as it was.
    for name in files:
        target = target_dir / name
        if target.exists():
            print(
                f"Error: {target} exists. Remove it or give another folder.",
                file=sys.stderr,
            )
            return 1
    written: list[Path] = []
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            target = target_dir / name
            # Before the write: a write that fails part way leaves a file.
            written.append(target)
            target.write_text(
                yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
    except OSError as exc:
        # A half-written folder would block the next run with "exists".
        for target in written:
            target.unlink(missing_ok=True)
        print(f"Error: cannot write to {target_dir} ({exc})", file=sys.stderr)
        return 1
    for target in written:
        print(f"Wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
