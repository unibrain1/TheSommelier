#!/usr/bin/env python3
"""Shared utilities for wine plan scripts.

Symbols exported:
  CURRENT_YEAR      — current calendar year
  PRO_SCORE_FIELDS  — professional critic score field names
  TYPE_TO_BADGE     — CellarTracker Type → plan badge mapping
  normalize         — canonical name normalizer (accent-aware)
  urgency_score     — bottle urgency priority (0 = most urgent)
  call_claude       — invoke Claude CLI with a prompt
  extract_json      — extract JSON object from LLM text response
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import date

import settings

# Ensure TZ env var is respected before using date.today()
if "TZ" in os.environ:
    time.tzset()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CURRENT_YEAR: int = date.today().year

# Map CellarTracker Type field to plan badge.
PRO_SCORE_FIELDS = ("WA", "WS", "BH", "AG", "JR", "JS", "JG")

TYPE_TO_BADGE: dict[str, str] = {
    "Red": "red",
    "White": "white",
    "Rosé": "rose",
    "Sparkling": "sparkling",
    "Sparkling - White": "sparkling",
    "White - Sparkling": "sparkling",
    "Sparkling - Rosé": "sparkling",
    "White - Sweet/Dessert": "white",
    "Red - Sweet/Dessert": "red",
}

# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------


def normalize(name: str) -> str:
    """Lowercase, strip accented characters and punctuation, collapse whitespace.

    Uses the most thorough variant: handles curly quotes, common accented
    characters (é, è, ñ), then strips remaining non-alphanumeric characters.
    """
    s = name.lower()
    s = re.sub(r"[''`]", "", s)  # curly/straight apostrophes
    s = s.replace("è", "e").replace("é", "e").replace("ñ", "n")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# Urgency scoring
# ---------------------------------------------------------------------------


def urgency_score(wine: dict) -> int:
    """Priority score — lower is more urgent.

    0  past peak      (EndConsume < current year)
    1  expiring now   (EndConsume == current year)
    2  expiring soon  (EndConsume == current year + 1)
    3  peak window    (BeginConsume <= current year <= EndConsume)
    4  entering window(BeginConsume == current year or current year + 1)
    5  long-ager / unknown
    """
    end = wine.get("EndConsume")
    begin = wine.get("BeginConsume")
    if end is not None and end < CURRENT_YEAR:
        return 0
    if end is not None and end == CURRENT_YEAR:
        return 1
    if end is not None and end == CURRENT_YEAR + 1:
        return 2
    if begin is not None and end is not None and begin <= CURRENT_YEAR <= end:
        return 3
    if begin is not None and begin in (CURRENT_YEAR, CURRENT_YEAR + 1):
        return 4
    return 5


# ---------------------------------------------------------------------------
# Default drinking windows for wines missing CellarTracker data
# ---------------------------------------------------------------------------

# Evaluation order matters — Sparkling before Color checks because CT lists
# sparkling wines with Color: White.
#
#   NV (vintage < 2000 or > current year) → now  to now+2
#   Sparkling                              → vintage to vintage+5
#   Sweet/Dessert                          → vintage to vintage+20
#   Rosé                                   → vintage to vintage+3
#   White                                  → vintage to vintage+5
#   Red                                    → vintage+2 to vintage+8


def apply_default_windows(inventory: list[dict]) -> int:
    """Fill in BeginConsume/EndConsume for wines missing both.

    Mutates wine dicts in place.  Returns the number of wines updated.
    """
    count = 0
    for wine in inventory:
        if wine.get("BeginConsume") is not None or wine.get("EndConsume") is not None:
            continue

        vintage = wine.get("Vintage", 0)
        wine_type = wine.get("Type", "")
        color = wine.get("Color", "")
        category = wine.get("Category", "")

        # Non-vintage: vintage missing, nonsensical, or outside 2000–current year
        if not vintage or vintage < 2000 or vintage > CURRENT_YEAR:
            begin, end = CURRENT_YEAR, CURRENT_YEAR + 2
        elif "Sparkling" in wine_type or category == "Sparkling":
            begin, end = vintage, vintage + 5
        elif category == "Sweet/Dessert":
            begin, end = vintage, vintage + 20
        elif color == "Rosé":
            begin, end = vintage, vintage + 3
        elif color == "White":
            begin, end = vintage, vintage + 5
        elif color == "Red":
            begin, end = vintage + 2, vintage + 8
        else:
            begin, end = vintage, vintage + 5

        wine["BeginConsume"] = begin
        wine["EndConsume"] = end
        wine["_defaultWindow"] = True
        count += 1

    return count


# ---------------------------------------------------------------------------
# Week lookup
# ---------------------------------------------------------------------------


def find_current_week(all_weeks: list[dict], target: date | None = None) -> dict | None:
    """Find the plan week containing the target date (default: today).

    Returns None if no matching week is found.
    """
    from datetime import datetime, timedelta

    target = target or date.today()
    for week in all_weeks:
        raw = week.get("date", "")
        try:
            week_monday = datetime.strptime(raw, "%b %d, %Y").date()
        except ValueError:
            continue
        if week_monday <= target < week_monday + timedelta(days=7):
            return week
    return None


# ---------------------------------------------------------------------------
# Claude CLI helpers (shared by generate_notes.py and enrich_menu.py)
# ---------------------------------------------------------------------------


def call_claude(prompt: str) -> str:
    """Call Claude CLI with a prompt and return the response."""
    try:
        result = subprocess.run(
            # No tools: the prompt holds untrusted text (menu, CellarTracker
            # and RSS notes), and the reply needs no file or shell access.
            # --tools takes a list, so "--" ends it before the prompt.
            ["claude", "--print", "--model", "haiku", "--tools", "", "--", prompt],
            capture_output=True,
            text=True,
            timeout=120,
            # Only the credential of the chosen mode. Other variables, such as
            # the CLAUDE_CODE_USE_* provider flags, pass through unchanged.
            env=settings.claude_env(os.environ),
        )
    except subprocess.TimeoutExpired:
        print("WARNING: Claude CLI timed out", file=sys.stderr)
        return ""
    except FileNotFoundError as e:
        # The CLI is not installed. Continue with empty notes, as for a timeout.
        print(f"WARNING: Claude CLI could not start: {e}", file=sys.stderr)
        return ""
    if result.returncode != 0:
        # The CLI writes auth errors ("Invalid API key") to stdout, not stderr.
        detail = result.stderr.strip() or result.stdout.strip()
        # Mask before the cut, so that a cut cannot leave part of a key.
        for name in settings.CLAUDE_CREDENTIALS:
            value = os.environ.get(name, "").strip()
            if value:
                detail = detail.replace(value, "***")
        detail = detail[:300]
        print(
            f"WARNING: Claude CLI returned {result.returncode}: {detail}",
            file=sys.stderr,
        )
        return ""
    return result.stdout.strip()


def extract_json(text: str) -> dict:
    """Extract JSON object from Claude's response."""
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        return {}
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError:
        print("WARNING: Could not parse Claude response as JSON", file=sys.stderr)
        return {}
