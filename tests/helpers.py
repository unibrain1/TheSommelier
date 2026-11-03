"""Shared test helpers: the inventory TSV layout and fixture builders.

The builders take dates from `date.today()`, so a fixture never goes out of
its drinking window or its week as time passes.
"""

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

# Minimal 22-field header: the base fields, without the score and valuation fields
ORIGINAL_FIELDS = [
    "iWine",
    "Vintage",
    "Wine",
    "Varietal",
    "MasterVarietal",
    "Type",
    "Color",
    "Category",
    "Country",
    "Region",
    "SubRegion",
    "Appellation",
    "Producer",
    "Designation",
    "Vineyard",
    "Location",
    "Bin",
    "Size",
    "BeginConsume",
    "EndConsume",
    "CT",
    "MY",
]

# Full header: the base fields plus the score and valuation fields
ALL_FIELDS = ORIGINAL_FIELDS + [
    "WA",
    "WS",
    "BH",
    "AG",
    "JR",
    "JS",
    "JG",
    "cNotes",
    "Price",
    "PurchaseDate",
    "StoreName",
    "Valuation",
]

# CellarTracker header-only files for the tables that the pipeline can do without.
NOTES_HEADER = ["iWine", "Wine", "TastingDate", "TastingNotes"]
FOODTAGS_HEADER = ["iWine", "Wine", "FoodTag"]
CONSUMED_HEADER = ["iWine", "Vintage", "Wine", "ConsumeDate", "ConsumeNote", "Type"]

LOCAL_TZ = ZoneInfo("America/Los_Angeles")


def minimal_row(overrides: dict | None = None) -> dict:
    """Return a dict representing a single bottle with sensible defaults."""
    base = {
        "iWine": "100001",
        "Vintage": "2018",
        "Wine": "Chateau Test",
        "Varietal": "Cabernet Sauvignon",
        "MasterVarietal": "Cabernet Sauvignon",
        "Type": "Red",
        "Color": "Red",
        "Category": "Wine",
        "Country": "USA",
        "Region": "Napa Valley",
        "SubRegion": "",
        "Appellation": "Napa Valley",
        "Producer": "Test Estate",
        "Designation": "",
        "Vineyard": "",
        "Location": "Cellar",
        "Bin": "A1",
        "Size": "750mL",
        "BeginConsume": "2022",
        "EndConsume": "2030",
        "CT": "92",
        "MY": "91",
    }
    if overrides:
        base.update(overrides)
    return base


def tsv_text(headers: list[str], rows: list[dict]) -> str:
    """Return TSV text with the given header and row dicts."""
    lines = ["\t".join(headers)]
    for row in rows:
        lines.append("\t".join(str(row.get(h, "")) for h in headers))
    return "\n".join(lines) + "\n"


def write_tsv(path: Path, headers: list[str], rows: list[dict]) -> Path:
    """Write a latin-1 TSV file, return its Path."""
    path.write_text(tsv_text(headers, rows), encoding="latin-1")
    return path


def inventory_rows(today: date | None = None) -> list[dict]:
    """Return six bottles (five wines, mixed types) with windows around today.

    The windows come from the current year, so each wine keeps its place in
    the plan: one past peak, one in its window, one that is still a long-ager.
    """
    year = (today or date.today()).year
    return [
        minimal_row(
            {
                "iWine": "200001",
                "Vintage": str(year - 8),
                "Wine": "Test Estate Cabernet",
                "BeginConsume": str(year - 3),
                "EndConsume": str(year + 4),
                "Bin": "A1",
            }
        ),
        # Same wine twice: the parser must count two bottles.
        minimal_row(
            {
                "iWine": "200001",
                "Vintage": str(year - 8),
                "Wine": "Test Estate Cabernet",
                "BeginConsume": str(year - 3),
                "EndConsume": str(year + 4),
                "Bin": "A2",
            }
        ),
        minimal_row(
            {
                "iWine": "200002",
                "Vintage": str(year - 2),
                "Wine": "Test Cellars Chardonnay",
                "Varietal": "Chardonnay",
                "MasterVarietal": "Chardonnay",
                "Type": "White",
                "Color": "White",
                "Region": "Sonoma",
                "Appellation": "Russian River Valley",
                "BeginConsume": str(year - 1),
                "EndConsume": str(year + 2),
                "Bin": "B1",
            }
        ),
        minimal_row(
            {
                "iWine": "200003",
                "Vintage": str(year - 1),
                "Wine": "Test Brut Sparkling",
                "Varietal": "Chardonnay",
                "MasterVarietal": "Chardonnay",
                "Type": "Sparkling",
                "Color": "White",
                "Country": "France",
                "Region": "Champagne",
                "Appellation": "Champagne",
                "BeginConsume": str(year),
                "EndConsume": str(year + 3),
                "Bin": "C1",
            }
        ),
        minimal_row(
            {
                "iWine": "200004",
                "Vintage": str(year - 1),
                "Wine": "Test Rose",
                "Varietal": "Grenache",
                "MasterVarietal": "Grenache",
                "Type": "Rosé",
                "Color": "Rosé",
                "Country": "France",
                "Region": "Provence",
                "Appellation": "Côtes de Provence",
                "BeginConsume": str(year - 1),
                "EndConsume": str(year + 1),
                "Bin": "D1",
            }
        ),
        minimal_row(
            {
                "iWine": "200005",
                "Vintage": str(year - 1),
                "Wine": "Test Reserve Barolo",
                "Varietal": "Nebbiolo",
                "MasterVarietal": "Nebbiolo",
                "Country": "Italy",
                "Region": "Piedmont",
                "Appellation": "Barolo",
                "BeginConsume": str(year + 8),
                "EndConsume": str(year + 20),
                "Bin": "E1",
            }
        ),
    ]


def build_inventory_tsv(path: Path, today: date | None = None, copies: int = 1) -> Path:
    """Write the inventory fixture with the full header.

    `copies` repeats each bottle row. The plan generator schedules one bottle
    for each of 52 weeks, so a pipeline run needs about 80 bottles. The parser
    tests use the default of 1.
    """
    rows = [
        {**row, "Bin": f"{row['Bin']}-{n}"}
        for row in inventory_rows(today)
        for n in range(copies)
    ]
    return write_tsv(path, ALL_FIELDS, rows)


def build_header_only_tsv(path: Path, headers: list[str]) -> Path:
    """Write a TSV file with a header and no rows."""
    return write_tsv(path, headers, [])


def build_ics(today: date | None = None) -> str:
    """Return an ICS calendar with two meals this week.

    The events start at 20:00 UTC (midday in Pacific time), so the Pacific
    date is the date that the builder gives, on both sides of the UTC offset.
    """
    day = today or datetime.now(LOCAL_TZ).date()
    events = [
        (day, "Grilled Steak with Roasted Potatoes", "Ribeye, rosemary, red wine jus"),
        (
            day + timedelta(days=1),
            "Lemon Chicken Piccata",
            "Pan-seared chicken, capers, lemon butter sauce",
        ),
    ]
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//The Sommelier tests//EN",
    ]
    for n, (d, summary, description) in enumerate(events):
        lines += [
            "BEGIN:VEVENT",
            f"UID:test-event-{n}@sommelier.test",
            "DTSTAMP:20200101T000000Z",
            f"DTSTART:{d.strftime('%Y%m%d')}T200000Z",
            f"DTEND:{d.strftime('%Y%m%d')}T210000Z",
            f"SUMMARY:{summary}",
            f"DESCRIPTION:{description}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def build_community_rss(iwine: str = "200001", today: date | None = None) -> str:
    """Return a small CellarTracker-style RSS feed with one community note."""
    day = today or date.today()
    tasted = f"{day.month}/{day.day}/{day.year}"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Notes</title>
<item>
<title>2018 Test Estate Cabernet</title>
<link>https://www.cellartracker.com/notes.asp?iWine={iwine}#iNote9000001</link>
<guid>https://www.cellartracker.com/notes.asp?iWine={iwine}#iNote9000001</guid>
<pubDate>Mon, 01 Jun 2026 10:00:00 GMT</pubDate>
<description>Tasted by tester. (93 pts.) Tasted {tasted}

Dark fruit, firm tannins, long finish.</description>
</item>
</channel></rss>
"""


def readme_rest_sensors() -> list[dict]:
    """Return the REST sensors of the README YAML under "Home Assistant"."""
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(
        encoding="utf-8"
    )
    section = readme.split("\n## Home Assistant\n", 1)[1].split("\n## ", 1)[0]
    block = re.search(r"```yaml\n(.*?)```", section, re.S)
    assert block, "no yaml block in the README Home Assistant section"
    return yaml.safe_load(block.group(1))["rest"]
