#!/usr/bin/env python3
"""Parse CellarTracker consumed history TSV into structured JSON.

Input:  consumed.tsv (tab-delimited, one row per consumed bottle)
Output: consumed.json to stdout — array of consumption records.
"""

import csv
import json
import sys
from pathlib import Path

KEEP_FIELDS = [
    "iWine",
    "Vintage",
    "Wine",
    "ConsumeDate",
    "ConsumeNote",
    "Varietal",
    "Type",
    "Country",
    "Region",
    "Appellation",
    "Producer",
]


def parse_consumed(tsv_path: str | Path) -> list[dict]:
    with open(tsv_path, encoding="latin-1") as f:
        reader = csv.DictReader(f, delimiter="\t")
        rows = list(reader)

    records = []
    for row in rows:
        record = {field: row.get(field, "").strip() or None for field in KEEP_FIELDS}
        records.append(record)

    # Sort by ConsumeDate descending (most recent first), nulls last
    # Relies on ISO 8601 format (YYYY-MM-DD) for correct lexicographic sort
    records.sort(key=lambda r: r["ConsumeDate"] or "", reverse=True)
    return records


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: parse_consumed.py <consumed.tsv>", file=sys.stderr)
        sys.exit(2)
    tsv = sys.argv[1]
    records = parse_consumed(tsv)
    json.dump(records, sys.stdout, indent=2, ensure_ascii=False)
    print()  # trailing newline
