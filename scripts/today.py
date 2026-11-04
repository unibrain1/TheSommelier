#!/usr/bin/env python3
"""Find today's menu and wine pairing from pairing_suggestions.json.

Input:  pairing_suggestions.json path (positional argument)
Output: today's entry as JSON to stdout, or {"date": ..., "meal": null}
        if no meal today.

When the menu feature is off, the pipeline does not make the input file.
A missing file then gives the same "no meal" output. When the feature is
on, a missing file is an error.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import settings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Output today's menu and pairing suggestion."
    )
    parser.add_argument("pairing_json", help="Path to pairing_suggestions.json")
    args = parser.parse_args()

    today_str = date.today().isoformat()
    pairing_path = Path(args.pairing_json)
    # With the menu off, no pairing file is the normal state, not a failure.
    if not pairing_path.exists() and not settings.feature("menu").on:
        suggestions = []
    else:
        data = json.loads(pairing_path.read_text())
        suggestions = data.get("suggestions", [])

    for entry in suggestions:
        if entry.get("date") == today_str:
            json.dump(entry, sys.stdout, indent=2, ensure_ascii=False)
            print()
            return

    # No meal planned today
    json.dump({"date": today_str, "meal": None}, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
