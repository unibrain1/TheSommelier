#!/usr/bin/env python3
"""Convert a v3 plan_config.py to a plan.yaml file. Run it one time, by hand.

Usage:
    python3 scripts/convert_plan_config.py scripts/plan_config.py config/plan.yaml

The script imports the Python file, so give it only your own plan_config.py.
It drops occasion_template, because the plan generator never read it. It
checks the result with plan_file.parse, so the output always loads. It does
not write over a file that exists.
"""

import importlib.util
import sys
from pathlib import Path

import yaml

import plan_file


def convert(source: Path) -> dict:
    """Return the plan.yaml data for one plan_config.py file."""
    spec = importlib.util.spec_from_file_location("plan_config_to_convert", source)
    if spec is None or spec.loader is None:
        raise plan_file.PlanConfigError(f"cannot load {source} as Python")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    data: dict = {}
    if hasattr(module, "HOLIDAYS"):
        data["holidays"] = [
            {"name": name, "month": month, "day": day}
            for name, month, day in module.HOLIDAYS
        ]
    if hasattr(module, "EVOLUTION_TRACKS"):
        data["evolution_tracks"] = [
            {
                "label": track["label"],
                "name_fragment": track["name_fragment"],
                "season_months": list(track["season_months"]),
                "preferred_month": track["preferred_month"],
            }
            for track in module.EVOLUTION_TRACKS
        ]
    plan_file.parse(data)
    return data


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "Usage: convert_plan_config.py <plan_config.py> <plan.yaml>",
            file=sys.stderr,
        )
        return 2
    source, target = Path(argv[0]), Path(argv[1])
    if not source.is_file():
        print(f"Error: file not found: {source}", file=sys.stderr)
        return 1
    if target.exists():
        print(
            f"Error: {target} exists. Remove it or give another path.", file=sys.stderr
        )
        return 1
    try:
        data = convert(source)
    # PlanConfigError is a ValueError.
    except (KeyError, TypeError, ValueError) as exc:
        print(f"Error: {source}: {exc}", file=sys.stderr)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    print(f"Wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
