#!/usr/bin/env python3
"""Deterministic wine plan generator.

Reads inventory.json and produces plan.json with allWeeks (52 entries),
quarterInfo, and changelog.  Before it writes, it reads the live
$SITE_DIR/plan.json.  If that file is valid JSON, the script copies it to
$DATA_DIR/plan_previous.json, compares the new plan with it for the
changelog, and copies its tasting notes to weeks with the same wine and
marks.  A live plan with a bad allWeeks list counts as no previous plan.
The run drops each changelog history entry with a bad shape.

Usage:
    python3 scripts/generate_plan.py "$DATA_DIR/inventory.json" "$DATA_DIR/plan.json"

Exit codes:
    0  the plan is written. No live plan at all is a first run, not a problem.
    1  no plan is written
    3  the plan is written, but the live plan could not be read, had no usable
       allWeeks list, or lost changelog history entries
"""

import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

# Ensure TZ env var is respected (Docker containers default to UTC)
if "TZ" in os.environ:
    time.tzset()

import plan_file
import settings
from generate_notes import valid_note
from scoring import composite_score
from wine_utils import (
    CURRENT_YEAR,
    PRO_SCORE_FIELDS,
    TYPE_TO_BADGE,
    apply_default_windows,
    normalize,
    urgency_score,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOTAL_WEEKS = 52


# Seasonal badge preferences (applied as tie-breakers / candidates filter)
# Maps season name → preferred badge list in priority order
SEASON_PREFER: dict[str, list[str]] = {
    "spring": ["sparkling", "rose", "white", "red"],
    "summer": ["sparkling", "rose", "white", "red"],
    "fall": ["red", "white", "sparkling", "rose"],
    "winter": ["red", "white", "sparkling", "rose"],
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def monday_of_week(d: date) -> date:
    """Return the Monday on or before *d*."""
    return d - timedelta(days=d.weekday())


def season_for_date(d: date) -> str:
    """Return the season name for a calendar date."""
    m = d.month
    if m in (3, 4):
        return "spring"
    if m == 5:
        # May 1–24 spring, May 25+ summer (Memorial Day shoulder)
        return "spring" if d.day < 25 else "summer"
    if m in (6, 7, 8):
        return "summer"
    if m in (9, 10, 11):
        return "fall"
    return "winter"  # 12, 1, 2


def format_date(d: date) -> str:
    """Format date as 'Mon D, YYYY' — matches existing plan.json."""
    return d.strftime("%b %-d, %Y")


def build_appellation(wine: dict) -> str:
    """Construct 'Country · Region · Appellation' string from inventory fields."""
    parts = []
    country = (wine.get("Country") or "").strip()
    region = (wine.get("Region") or "").strip()
    sub_region = (wine.get("SubRegion") or "").strip()
    appellation = (wine.get("Appellation") or "").strip()

    if country:
        parts.append(country)
    if region:
        parts.append(region)
    # Prefer the most specific geographic unit available
    if appellation and appellation.lower() not in ("unknown", ""):
        parts.append(appellation)
    elif sub_region and sub_region.lower() not in ("unknown", ""):
        parts.append(sub_region)

    return " · ".join(parts)


def build_window(wine: dict) -> str | None:
    """Return 'Begin–End' window string, or None if data is absent."""
    begin = wine.get("BeginConsume")
    end = wine.get("EndConsume")
    if begin is None and end is None:
        return None
    b = str(begin) if begin is not None else "?"
    e = str(end) if end is not None else "?"
    return f"{b}–{e}"


def build_score(wine: dict) -> str | None:
    """Return best available critic score label (e.g., 'WA92', 'CT90'), or None."""
    for field in PRO_SCORE_FIELDS:
        val = wine.get(field)
        if val is not None:
            return f"{field}{val:g}"
    ct = wine.get("CT")
    if ct is None:
        return None
    return f"CT{ct:g}"


def build_location(wine: dict) -> str:
    """Return 'Location / Bin' string for cellar pick list."""
    parts = [wine.get("Location", ""), wine.get("Bin", "")]
    return " / ".join(p for p in parts if p and p != "Unknown")


def is_long_ager(wine: dict) -> bool:
    """Return True if this wine should be held — BeginConsume > current_year + 2."""
    begin = wine.get("BeginConsume")
    return begin is not None and begin > CURRENT_YEAR + 2


def max_schedulable(wine: dict) -> int:
    """Maximum bottles of this wine that may appear in the plan.

    Hold-back rule: if 3+ bottles of the same wine, hold at least
    2 back for future drinking (schedule at most qty - 2, minimum 1).
    """
    qty = wine.get("Quantity", 1)
    if qty <= 0:
        return 0
    if qty >= 3:
        return max(1, qty - 2)
    return qty


# ---------------------------------------------------------------------------
# Week schedule generation
# ---------------------------------------------------------------------------


def generate_week_dates(start: date) -> list[date]:
    """Generate TOTAL_WEEKS Monday dates starting from *start*."""
    return [start + timedelta(weeks=i) for i in range(TOTAL_WEEKS)]


def find_nearest_week_index(
    dates: list[date], target_month: int, target_day: int, year_offset: int = 0
) -> int:
    """Find the index in *dates* whose date is nearest to the given month/day.

    *year_offset* 0 searches within year 1 of the plan; 1 searches year 2.
    """
    base_year = dates[0].year + year_offset
    try:
        target = date(base_year, target_month, target_day)
    except ValueError:
        # Clamp invalid dates (e.g., Feb 30)
        import calendar

        last_day = calendar.monthrange(base_year, target_month)[1]
        target = date(base_year, target_month, min(target_day, last_day))

    best_idx = 0
    best_delta = abs((dates[0] - target).days)
    for i, d in enumerate(dates):
        delta = abs((d - target).days)
        if delta < best_delta:
            best_delta = delta
            best_idx = i
    return best_idx


# ---------------------------------------------------------------------------
# Candidate building
# ---------------------------------------------------------------------------


def build_candidates(inventory: list[dict]) -> list[dict]:
    """Filter and sort inventory wines into a schedulable candidate list.

    Wines that are long-agers AND whose EndConsume is beyond the 2-year
    planning horizon are excluded entirely.

    Sort order:
      1. composite_score ascending (most desirable first; uses neutral season/empty placed)
      2. EndConsume ascending (tightest window first)
      3. Wine name ascending (deterministic tie-break)
    """
    candidates = []
    for wine in inventory:
        score = urgency_score(wine)
        # Exclude wines not yet in their window and beyond current year + 2
        if score == 5:
            end = wine.get("EndConsume")
            begin = wine.get("BeginConsume")
            # Allow if EndConsume is within the planning horizon
            if end is not None and end <= CURRENT_YEAR + 2:
                pass  # borderline long-ager — allow
            elif begin is not None and begin <= CURRENT_YEAR + 2:
                pass  # just entering — allow
            else:
                continue  # true long-ager — skip

        if max_schedulable(wine) <= 0:
            continue

        candidates.append(wine)

    candidates.sort(
        key=lambda w: (
            composite_score(w, "summer", week_index=0, placed=[]),
            w.get("EndConsume") or 9999,
            w.get("Wine", ""),
        )
    )
    return candidates


# ---------------------------------------------------------------------------
# Evolution track scheduling
# ---------------------------------------------------------------------------


def find_evolution_vintage(
    track: dict,
    candidates: list[dict],
    scheduled_counts: dict[str, int],
    year_index: int,  # 0-based plan year (0 or 1)
) -> dict | None:
    """Pick the most urgent schedulable bottle for an evolution track.

    Prefer vintages within their drinking window, most urgent first.
    """
    frag = normalize(track["name_fragment"])
    matches = []
    for wine in candidates:
        if frag not in normalize(wine.get("Wine", "")):
            continue
        key = f"{wine.get('Vintage', '')}|{wine.get('Wine', '')}"
        already = scheduled_counts.get(key, 0)
        if already >= max_schedulable(wine):
            continue
        score = urgency_score(wine)
        if score == 5:
            continue  # don't use true long-agers for evolution
        matches.append(wine)

    if not matches:
        return None

    # Sort by urgency, then EndConsume ascending, then vintage descending
    # (newer vintages are more interesting for evolution tracking)
    matches.sort(
        key=lambda w: (
            urgency_score(w),
            w.get("EndConsume") or 9999,
            -(w.get("Vintage") or 0),
        )
    )
    return matches[0]


# ---------------------------------------------------------------------------
# Holiday anchor scheduling
# ---------------------------------------------------------------------------


def assign_holiday_anchors(
    week_dates: list[date],
    candidates: list[dict],
    reserved: dict[int, dict],  # week_index → wine
    scheduled_counts: dict[str, int],
    placed: list[dict | None] | None = None,
    *,
    holidays: list[tuple[str, int, int]],
) -> None:
    """For each holiday, find the nearest unoccupied week and reserve a bottle.

    Holidays whose target date falls before the plan start are scheduled in
    the second plan year instead.  Modifies *reserved* and *scheduled_counts*
    in place.
    """
    plan_start = week_dates[0]
    plan_year1 = plan_start.year

    # Collect occasions already covered by evolution track reservations
    evolution_occasions: set[str] = set()
    for slot in reserved.values():
        occ = slot.get("occasion", "") or ""
        for holiday_name, _, _ in holidays:
            if holiday_name.lower() in occ.lower():
                evolution_occasions.add(holiday_name)

    for holiday_name, month, day in holidays:
        # Skip if an evolution track already anchored this holiday
        if holiday_name in evolution_occasions:
            continue
        # Try both plan years; prefer year 1 if the date hasn't passed
        for year_offset in (0, 1):
            calendar_year = plan_year1 + year_offset
            try:
                target = date(calendar_year, month, day)
            except ValueError:
                import calendar as cal_mod

                last_day = cal_mod.monthrange(calendar_year, month)[1]
                target = date(calendar_year, month, min(day, last_day))

            # Skip if the holiday has already passed for this year
            if target < plan_start:
                continue

            idx = find_nearest_week_index(week_dates, month, day, year_offset)

            # Verify the found week is within ±3 weeks of the actual target
            if abs((week_dates[idx] - target).days) > 21:
                continue

            if idx in reserved:
                # Try adjacent weeks (up to ±3)
                found_slot = False
                for delta in (1, -1, 2, -2, 3, -3):
                    alt = idx + delta
                    if 0 <= alt < TOTAL_WEEKS and alt not in reserved:
                        idx = alt
                        found_slot = True
                        break
                if not found_slot:
                    continue

            season = season_for_date(week_dates[idx])
            # Pick the best seasonal bottle that isn't already fully scheduled
            best = _pick_best_for_slot(
                candidates,
                scheduled_counts,
                season,
                week_index=idx,
                placed=placed,
                exclude_keys=set(reserved_keys(reserved)),
            )
            if best is None:
                continue

            key = wine_key(best)
            scheduled_counts[key] = scheduled_counts.get(key, 0) + 1
            reserved[idx] = {"wine": best, "special": True, "occasion": holiday_name}
            break


def reserved_keys(reserved: dict[int, dict]) -> set[str]:
    """Return the set of wine keys already reserved."""
    keys = set()
    for slot in reserved.values():
        w = slot.get("wine")
        if w:
            keys.add(wine_key(w))
    return keys


def wine_key(wine: dict) -> str:
    return f"{wine.get('Vintage', '')}|{wine.get('Wine', '')}"


def _pick_best_for_slot(
    candidates: list[dict],
    scheduled_counts: dict[str, int],
    season: str,
    week_index: int = 0,
    placed: list[dict | None] | None = None,
    exclude_keys: set[str] | None = None,
    required_fragment: str | None = None,
    community_notes: dict[str, list[dict]] | None = None,
) -> dict | None:
    """Pick the best available bottle for a given slot using composite scoring."""
    exclude_keys = exclude_keys or set()
    pool = []
    for wine in candidates:
        key = wine_key(wine)
        if key in exclude_keys:
            continue
        if scheduled_counts.get(key, 0) >= max_schedulable(wine):
            continue
        if required_fragment and required_fragment not in normalize(
            wine.get("Wine", "")
        ):
            continue
        pool.append(wine)

    if not pool:
        return None

    pool.sort(
        key=lambda w: (
            composite_score(w, season, week_index, placed or [], community_notes),
            w.get("EndConsume") or 9999,
            w.get("Wine", ""),
        )
    )
    return pool[0]


# ---------------------------------------------------------------------------
# Quarter info generation
# ---------------------------------------------------------------------------

_SEASON_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "spring": {
        "y1": "Urgent opens · lighter whites & rosés warming up",
        "y2": "Expiring bottles cleared · lighter reds & whites",
    },
    "summer": {
        "y1": "Rosés, whites & celebratory bottles",
        "y2": "Sparkling, whites & lighter Pinots",
    },
    "fall": {
        "y1": "Transition to reds — Oregon, Washington, Italian",
        "y2": "Bold reds · evolution tracks continue",
    },
    "winter": {
        "y1": "Big reds, Barolo, Bordeaux & evolution tracks",
        "y2": "Louise & Roserock evolution · Adamant Cabs",
    },
}

_SEASON_MONTHS: dict[str, str] = {
    "spring": "March – May",
    "summer": "June – August",
    "fall": "September – November",
    "winter": "December – March",
}


_SEASON_ORDER: dict[str, int] = {"spring": 0, "summer": 1, "fall": 2, "winter": 3}


def build_quarter_info(week_dates: list[date]) -> dict:
    """Build the quarterInfo object from actual week dates.

    When the plan starts mid-year (e.g. summer), seasons that occur earlier in
    the calendar year (e.g. spring) actually fall in the *next* calendar year
    within year-1 of the plan.  Adjust titles accordingly.
    """
    y1_year = week_dates[0].year
    y2_year = y1_year + 1
    start_idx = _SEASON_ORDER[season_for_date(week_dates[0])]

    quarter_info: dict[str, dict] = {}
    for season in ("spring", "summer", "fall", "winter"):
        sub = _SEASON_MONTHS[season]
        desc = _SEASON_DESCRIPTIONS[season]

        # Seasons that precede the start season in the cycle land in the next
        # calendar year within the same plan year (e.g. Spring 2027 in y1 of a
        # plan that starts in Summer 2026).
        year_bump = 1 if _SEASON_ORDER[season] < start_idx else 0

        if season == "winter":
            base1 = y1_year + year_bump
            base2 = y2_year + year_bump
            y1_title = f"Winter {base1}–{base1 + 1}"
            y2_title = f"Winter {base2}–{base2 + 1}"
        else:
            y1_title = f"{season.capitalize()} {y1_year + year_bump}"
            y2_title = f"{season.capitalize()} {y2_year + year_bump}"

        quarter_info[season] = {
            "y1": {"title": y1_title, "sub": sub, "note": desc["y1"]},
            "y2": {"title": y2_title, "sub": sub, "note": desc["y2"]},
        }
    return quarter_info


# ---------------------------------------------------------------------------
# Changelog diffing
# ---------------------------------------------------------------------------


def plan_key(w: dict) -> str:
    """Return the wine identity of a plan week: vintage and name."""
    return f"{w.get('vintage', '')}|{w.get('name', '')}"


def diff_plans(old_weeks: list[dict], new_weeks: list[dict]) -> list[dict]:
    """Compare two allWeeks lists and return changelog entries."""
    old_by_key: dict[str, list[int]] = {}
    for w in old_weeks:
        k = plan_key(w)
        old_by_key.setdefault(k, []).append(w.get("week", 0))

    new_by_key: dict[str, list[int]] = {}
    for w in new_weeks:
        k = plan_key(w)
        new_by_key.setdefault(k, []).append(w.get("week", 0))

    changes: list[dict] = []

    all_keys = set(old_by_key) | set(new_by_key)
    for key in sorted(all_keys):
        old_weeks_list = old_by_key.get(key, [])
        new_weeks_list = new_by_key.get(key, [])
        vintage, name = key.split("|", 1)
        label = f"{vintage} {name}"

        for wk in sorted(set(old_weeks_list) - set(new_weeks_list)):
            changes.append(
                {"type": "removed", "week": wk, "description": f"{label} removed"}
            )
        for wk in sorted(set(new_weeks_list) - set(old_weeks_list)):
            changes.append(
                {"type": "added", "week": wk, "description": f"{label} added"}
            )

    return changes


# ---------------------------------------------------------------------------
# Note reuse
# ---------------------------------------------------------------------------


def usable_weeks(value: object) -> list[dict] | None:
    """Return the allWeeks value of a live plan, or None if its shape is bad.

    The changelog sorts the weeks by `week`, and the note reuse sorts by
    `week` and keys on `occasion`. So each week must be a dict with an int
    `week` and a str or None `occasion`.
    """
    if not isinstance(value, list):
        return None
    for w in value:
        if not isinstance(w, dict):
            return None
        week = w.get("week")
        if not isinstance(week, int) or isinstance(week, bool):
            return None
        if not isinstance(w.get("occasion"), (str, type(None))):
            return None
    return value


def usable_history(value: object) -> list[dict]:
    """Return the changelog entries of a live plan that have a usable shape.

    main() compares each `date` with a cutoff string, so a `date` that is not
    a str would stop the run. The site reads `type` from each item of
    `changes`, so `changes` must be a list of dicts. The run drops the other
    entries.
    """
    if not isinstance(value, list):
        return []
    return [
        e
        for e in value
        if isinstance(e, dict)
        and isinstance(e.get("date"), str)
        and isinstance(e.get("changes"), list)
        and all(isinstance(c, dict) for c in e["changes"])
    ]


def note_key(w: dict) -> tuple[str, bool, bool, str | None]:
    """Key a week by its wine and its marks: urgent, evolution and occasion.

    The window, scores and CellarTracker notes also go into the note prompt,
    but they are not in the key. They change rarely, and a key on them would
    make Claude write a note again for a small change.
    """
    return (
        plan_key(w),
        bool(w.get("urgent")),
        bool(w.get("evolution")),
        w.get("occasion") or None,
    )


def reuse_notes(old_weeks: list[dict] | None, new_weeks: list[dict]) -> int:
    """Set the note of each new week from an old week with the same key.

    Only a note that valid_note() accepts goes to a new week. Each old note
    goes to one new week, lowest old week number first. A key with more new
    weeks than old notes leaves the extra weeks empty. When Claude is on,
    generate_notes.py writes a note for them. Returns the count of reused
    notes.
    """
    old_notes: dict[tuple, list[str]] = {}
    for w in sorted(old_weeks or [], key=lambda w: w.get("week", 0)):
        note = valid_note(w.get("note"))
        if note:
            old_notes.setdefault(note_key(w), []).append(note)

    reused = 0
    for w in new_weeks:
        notes = old_notes.get(note_key(w))
        if notes:
            w["note"] = notes.pop(0)
            reused += 1
    return reused


# ---------------------------------------------------------------------------
# Plan entry builder
# ---------------------------------------------------------------------------


def make_entry(
    week_num: int,
    plan_year: int,
    week_date: date,
    wine: dict,
    special: bool = False,
    evolution: bool = False,
    occasion: str | None = None,
) -> dict:
    """Assemble a single plan entry dict from an inventory wine."""
    end = wine.get("EndConsume")
    urgent = end is not None and end <= CURRENT_YEAR + 1

    badge = TYPE_TO_BADGE.get(wine.get("Type", ""), "red")

    return {
        "week": week_num,
        "year": plan_year,
        "date": format_date(week_date),
        "season": season_for_date(week_date),
        "badge": badge,
        "vintage": str(wine.get("Vintage", "")),
        "name": wine.get("Wine", ""),
        "appellation": build_appellation(wine),
        "window": build_window(wine),
        "score": build_score(wine),
        "urgent": urgent,
        "special": special,
        "evolution": evolution,
        "occasion": occasion,
        "note": "",
        "location": build_location(wine),
    }


# ---------------------------------------------------------------------------
# Main scheduling logic
# ---------------------------------------------------------------------------


def _evolution_target_date(preferred_month: int, year: int) -> date:
    """Return the target anchor date for an evolution slot in a given year."""
    try:
        return date(year, preferred_month, 15)
    except ValueError:
        return date(year, preferred_month, 28)


def schedule_evolution_tracks(
    week_dates: list[date],
    candidates: list[dict],
    reserved: dict[int, dict],
    scheduled_counts: dict[str, int],
    *,
    evolution_tracks: list[dict],
    holidays: list[tuple[str, int, int]],
) -> None:
    """Reserve one week per year per evolution track.

    If the preferred month for year-1 has already passed (i.e. the target date
    falls before the plan start), that track is only scheduled in year 2.

    Modifies *reserved* and *scheduled_counts* in place.
    """
    plan_start = week_dates[0]
    plan_year1_calendar = plan_start.year  # e.g. 2026

    for year_offset in (0, 1):
        calendar_year = plan_year1_calendar + year_offset
        for track in evolution_tracks:
            preferred_month = track["preferred_month"]
            target = _evolution_target_date(preferred_month, calendar_year)

            # If the target date is before the plan start, this track cannot
            # be scheduled in year 1 — skip it (will be handled in year 2).
            if target < plan_start:
                continue

            wine = find_evolution_vintage(
                track, candidates, scheduled_counts, year_offset
            )
            if wine is None:
                continue

            # Find the week index nearest to the target date
            idx = find_nearest_week_index(week_dates, preferred_month, 15, year_offset)

            # Verify the found week is actually in the correct calendar year
            # (find_nearest_week_index may overshoot into the adjacent year)
            if week_dates[idx].year != calendar_year:
                # Accept it if it's within the plan and within ±6 weeks of
                # the month boundary (e.g. late-Jan / early-Mar for February)
                if abs((week_dates[idx] - target).days) > 42:
                    continue

            # Adjust if slot is already taken — search within the season window
            if idx in reserved:
                season_months = track["season_months"]
                placed = False
                for delta in range(1, 10):
                    for sign in (1, -1):
                        alt = idx + sign * delta
                        if 0 <= alt < TOTAL_WEEKS and alt not in reserved:
                            alt_date = week_dates[alt]
                            if alt_date.month in range(
                                season_months[0], season_months[1] + 1
                            ):
                                idx = alt
                                placed = True
                                break
                    if placed:
                        break

            if idx in reserved:
                continue  # couldn't find a free slot

            # Determine occasion label — check if a holiday anchor is nearby
            nearby_holiday = None
            for holiday_name, month, day in holidays:
                try:
                    h_target = date(week_dates[idx].year, month, day)
                except ValueError:
                    continue
                if abs((week_dates[idx] - h_target).days) <= 7:
                    nearby_holiday = holiday_name
                    break

            occasion = (
                f"{nearby_holiday} — {track['label']} evolution"
                if nearby_holiday
                else f"{track['label']} evolution Year {year_offset + 1}"
            )

            key = wine_key(wine)
            scheduled_counts[key] = scheduled_counts.get(key, 0) + 1
            reserved[idx] = {
                "wine": wine,
                "special": True,
                "evolution": True,
                "occasion": occasion,
            }


def _load_community_notes() -> dict[str, list[dict]] | None:
    """Load community notes cache if available."""
    cn_path = settings.data_dir() / "community_notes.json"
    if cn_path.exists() and cn_path.stat().st_size > 0:
        try:
            return json.loads(cn_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(
                f"WARNING: Could not load community notes: {exc}",
                file=sys.stderr,
            )
    return None


def generate_plan(inventory: list[dict], plan_config: plan_file.PlanConfig) -> dict:
    """Generate the full 52-week plan from inventory data."""
    today = date.today()
    start = monday_of_week(today)
    week_dates = generate_week_dates(start)

    community_notes = _load_community_notes()
    candidates = build_candidates(inventory)

    # Track how many bottles of each wine we've already scheduled
    scheduled_counts: dict[str, int] = {}

    # reserved[week_index] = {"wine": ..., "special": ..., "evolution": ..., "occasion": ...}
    reserved: dict[int, dict] = {}

    # Track placed wines for diversity scoring
    placed: list[dict | None] = [None] * TOTAL_WEEKS

    # --- Phase 1: Reserve evolution tracks ---
    schedule_evolution_tracks(
        week_dates,
        candidates,
        reserved,
        scheduled_counts,
        evolution_tracks=plan_config.evolution_tracks,
        holidays=plan_config.holidays,
    )

    # Populate placed[] with evolution-track wines so holiday diversity scoring is accurate
    for idx, slot in reserved.items():
        placed[idx] = slot["wine"]

    # --- Phase 2: Reserve holiday anchors ---
    assign_holiday_anchors(
        week_dates,
        candidates,
        reserved,
        scheduled_counts,
        placed=placed,
        holidays=plan_config.holidays,
    )

    # Update placed[] with holiday anchor wines
    for idx, slot in reserved.items():
        placed[idx] = slot["wine"]

    # --- Phase 3: Fill remaining weeks ---
    # For weeks 0-7 (first 8): strongly prefer urgent/past-peak regardless of season
    # For weeks 8-15 (next 8): prefer expiring-this-year
    # After that: seasonal preference applies

    all_weeks: list[dict] = []

    # Detect when the season cycles back to the start — those weeks are year 2
    start_season = season_for_date(week_dates[0])
    seen_seasons: set[str] = set()
    plan_year = 1

    for i, week_date in enumerate(week_dates):
        week_num = i + 1
        season = season_for_date(week_date)
        if season == start_season and len(seen_seasons) >= 3:
            plan_year = 2
        seen_seasons.add(season)

        if i in reserved:
            slot = reserved[i]
            wine = slot["wine"]
            entry = make_entry(
                week_num,
                plan_year,
                week_date,
                wine,
                special=slot.get("special", False),
                evolution=slot.get("evolution", False),
                occasion=slot.get("occasion"),
            )
            all_weeks.append(entry)
            continue

        # Determine which wine to place here
        if i < 8:
            # Phase A: past-peak and urgent bottles first
            wine = _pick_for_urgent_phase(
                candidates,
                scheduled_counts,
                season,
                max_urgency=1,
                week_index=i,
                placed=placed,
                community_notes=community_notes,
            )
        elif i < 16:
            # Phase B: expiring this/next year
            wine = _pick_for_urgent_phase(
                candidates,
                scheduled_counts,
                season,
                max_urgency=2,
                week_index=i,
                placed=placed,
                community_notes=community_notes,
            )
        else:
            # Phase C: composite scoring
            wine = _pick_best_for_slot(
                candidates,
                scheduled_counts,
                season,
                week_index=i,
                placed=placed,
                community_notes=community_notes,
            )

        if wine is None:
            # Fallback: any available bottle
            wine = _pick_best_for_slot(
                candidates,
                scheduled_counts,
                season,
                week_index=i,
                placed=placed,
                community_notes=community_notes,
            )

        if wine is None:
            # Nothing left — this should not happen if inventory is sufficient
            raise RuntimeError(
                f"Ran out of schedulable bottles at week {week_num}. "
                f"Check inventory.json."
            )

        key = wine_key(wine)
        scheduled_counts[key] = scheduled_counts.get(key, 0) + 1
        placed[i] = wine
        entry = make_entry(week_num, plan_year, week_date, wine)
        all_weeks.append(entry)

    quarter_info = build_quarter_info(week_dates)

    return {
        "allWeeks": all_weeks,
        "quarterInfo": quarter_info,
    }


def _pick_for_urgent_phase(
    candidates: list[dict],
    scheduled_counts: dict[str, int],
    season: str,
    max_urgency: int,
    week_index: int = 0,
    placed: list[dict | None] | None = None,
    community_notes: dict[str, list[dict]] | None = None,
) -> dict | None:
    """Pick the best bottle at or below *max_urgency* tier, sorted by composite score."""
    pool = []
    for wine in candidates:
        key = wine_key(wine)
        if scheduled_counts.get(key, 0) >= max_schedulable(wine):
            continue
        if urgency_score(wine) <= max_urgency:
            pool.append(wine)
    if not pool:
        return None
    pool.sort(
        key=lambda w: (
            composite_score(w, season, week_index, placed or [], community_notes),
            w.get("EndConsume") or 9999,
            w.get("Wine", ""),
        )
    )
    return pool[0]


# ---------------------------------------------------------------------------
# Changelog helpers
# ---------------------------------------------------------------------------


def build_changelog(old_weeks: list[dict] | None, new_weeks: list[dict]) -> dict:
    today_str = date.today().strftime("%Y-%m-%d")
    if old_weeks is None:
        return {
            "date": today_str,
            "summary": "Initial plan generated from CellarTracker inventory.",
            "changes": [],
        }

    changes = diff_plans(old_weeks, new_weeks)
    if changes:
        summary = (
            f"Synced with CellarTracker inventory. {len(changes)} change(s) detected."
        )
    else:
        summary = "Synced with CellarTracker inventory. No scheduling changes."

    return {
        "date": today_str,
        "summary": summary,
        "changes": changes,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    if len(sys.argv) < 3:
        print(
            "Usage: generate_plan.py <inventory.json> <plan.json>",
            file=sys.stderr,
        )
        sys.exit(1)

    inventory_path = Path(sys.argv[1])
    plan_path = Path(sys.argv[2])
    previous_path = settings.data_dir() / "plan_previous.json"

    if not inventory_path.exists():
        print(f"Error: inventory file not found: {inventory_path}", file=sys.stderr)
        sys.exit(1)

    # Before any write, so a bad plan file leaves the live plan as it is.
    try:
        plan_config = plan_file.load(settings.config_dir())
    except plan_file.PlanConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    inventory: list[dict] = json.loads(inventory_path.read_text(encoding="utf-8"))

    # The live site plan gives the changelog diff and the notes to reuse.
    live_plan_path = settings.site_dir() / "plan.json"
    old_weeks: list[dict] | None = None
    old_history: list[dict] = []
    old_data = None
    # True when the live plan exists but its history or notes are lost.
    previous_lost = False
    if live_plan_path.exists():
        try:
            old_data = json.loads(live_plan_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"WARNING: could not read existing plan ({exc})", file=sys.stderr)
            previous_lost = True
    else:
        print(f"Note: no live plan at {live_plan_path}, changelog history starts empty")
    if old_data is not None:
        # A bad file must not stop every later sync. The first good run
        # writes over it.
        weeks = old_data.get("allWeeks") if isinstance(old_data, dict) else None
        old_weeks = usable_weeks(weeks)
        if old_weeks is None:
            print(
                "WARNING: existing plan has no usable allWeeks list, "
                "so the run uses no previous plan and no changelog history",
                file=sys.stderr,
            )
            previous_lost = True
        else:
            history = old_data.get("changelogHistory") or []
            old_history = usable_history(history)
            dropped = (
                len(history) - len(old_history) if isinstance(history, list) else 1
            )
            if dropped:
                print(
                    f"WARNING: dropped {dropped} changelog history entries "
                    "with a bad shape from the existing plan",
                    file=sys.stderr,
                )
                previous_lost = True
        try:
            previous_path.parent.mkdir(parents=True, exist_ok=True)
            previous_path.write_text(
                json.dumps(old_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"Previous plan backed up to {previous_path}")
        except OSError as exc:
            print(f"Warning: could not back up plan ({exc})", file=sys.stderr)

    # Apply default drinking windows for wines missing CellarTracker data
    defaulted = apply_default_windows(inventory)
    if defaulted:
        print(f"Applied default drinking windows to {defaulted} wine(s)")

    print(f"Generating plan from {len(inventory)} inventory wines…")
    plan_data = generate_plan(inventory, plan_config)
    reused = reuse_notes(old_weeks, plan_data["allWeeks"])
    no_note = sum(1 for w in plan_data["allWeeks"] if not w["note"])

    changelog = build_changelog(old_weeks, plan_data["allWeeks"])

    # Accumulate changelog history (only if there are actual changes)
    changelog_history = list(old_history)  # copy
    if changelog["changes"]:
        changelog_history.insert(0, changelog)  # newest first

    # Prune entries older than 90 days
    cutoff = (date.today() - timedelta(days=90)).isoformat()
    changelog_history = [
        entry for entry in changelog_history if entry.get("date", "") >= cutoff
    ]

    plan_data["changelogHistory"] = changelog_history
    plan_data["changelog"] = changelog  # keep for backward compat

    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        json.dumps(plan_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    added = sum(1 for c in changelog["changes"] if c["type"] == "added")
    removed = sum(1 for c in changelog["changes"] if c["type"] == "removed")

    print(f"Plan written to {plan_path}")
    print(f"  {len(plan_data['allWeeks'])} weeks scheduled")
    print(f"  Changelog: +{added} added, -{removed} removed")
    print(
        f"  Notes: {reused} reused from the previous plan, {no_note} weeks have no note"
    )
    if previous_lost:
        # pipeline.sh counts exit 3 as a warning and goes on with this plan.
        sys.exit(3)


if __name__ == "__main__":
    main()
