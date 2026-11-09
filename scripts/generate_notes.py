#!/usr/bin/env python3
"""Generate tasting notes for plan entries using Claude Code CLI.

Reads plan.json and optionally CellarTracker notes/food tags and community
notes to generate contextual tasting notes. Claude augments any existing
CT tasting notes, community tasting notes, and food pairing data.

Needs one Claude key in the environment: ANTHROPIC_API_KEY (API key mode) or
CLAUDE_CODE_OAUTH_TOKEN (subscription mode). pipeline.sh runs this script only
when settings.py reports the claude feature on. wine_utils.call_claude passes
the key of the chosen mode to the CLI.

Usage: generate_notes.py <plan.json> [notes.tsv] [foodtags.tsv] [community_notes.json] [inventory.json]
"""

import csv
import json
import sys
from pathlib import Path

from wine_utils import call_claude, extract_json

BATCH_SIZE = 20  # bottles per Claude call to stay within context
# The prompt asks for 1-2 sentences. A longer reply is not a note, and the
# site publishes plan.json with no login.
MAX_NOTE_CHARS = 400


def valid_note(note: object) -> str | None:
    """Return the note, stripped, if it is a usable string. Otherwise None."""
    if not isinstance(note, str):
        return None
    note = note.strip()
    if not note or len(note) > MAX_NOTE_CHARS:
        return None
    return note


def parse_ct_notes(notes_path: str) -> dict[str, list[str]]:
    """Parse CellarTracker tasting notes TSV, indexed by iWine."""
    notes_by_wine: dict[str, list[str]] = {}
    path = Path(notes_path)
    if not path.exists() or path.stat().st_size == 0:
        return notes_by_wine
    try:
        with open(path, encoding="latin-1") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                iwine = row.get("iWine", "")
                note = row.get("Note", row.get("TastingNote", "")).strip()
                if iwine and note:
                    notes_by_wine.setdefault(iwine, []).append(note)
    except Exception as e:
        print(f"WARNING: Could not parse notes: {e}", file=sys.stderr)
    return notes_by_wine


def parse_ct_foodtags(foodtags_path: str) -> dict[str, list[str]]:
    """Parse CellarTracker food tags TSV, indexed by iWine."""
    tags_by_wine: dict[str, list[str]] = {}
    path = Path(foodtags_path)
    if not path.exists() or path.stat().st_size == 0:
        return tags_by_wine
    try:
        with open(path, encoding="latin-1") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                iwine = row.get("iWine", "")
                tag = row.get("Tag", row.get("FoodTag", "")).strip()
                if iwine and tag:
                    tags_by_wine.setdefault(iwine, []).append(tag)
    except Exception as e:
        print(f"WARNING: Could not parse food tags: {e}", file=sys.stderr)
    return tags_by_wine


def load_inventory_index(inv_path: str | None) -> dict[str, str]:
    """Build a rough vintage+name → iWine index from inventory.json (if available)."""
    index = {}
    if not inv_path:
        return index
    try:
        inventory = json.loads(Path(inv_path).read_text(encoding="utf-8"))
        for wine in inventory:
            key = f"{wine.get('Vintage', '')}|{wine.get('Wine', '')}".lower()
            index[key] = str(wine.get("iWine", ""))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        print(f"WARNING: Could not load inventory index: {e}", file=sys.stderr)
    return index


def find_iwine(vintage: str, name: str, inv_index: dict[str, str]) -> str:
    """Look up iWine for a plan entry."""
    key = f"{vintage}|{name}".lower()
    if key in inv_index:
        return inv_index[key]
    # Fuzzy: check if plan name is substring of inventory name or vice versa
    name_lower = name.lower()
    for k, iwine in inv_index.items():
        _, inv_name = k.split("|", 1)
        if vintage.lower() in k and (name_lower in inv_name or inv_name in name_lower):
            return iwine
    return ""


def load_community_notes(path: str | None) -> dict[str, list[dict]]:
    """Load community notes JSON cache, indexed by iWine."""
    if not path:
        return {}
    from fetch_community_notes import load_cache

    return load_cache(Path(path))


def build_prompt(
    entries: list[dict],
    ct_notes: dict[str, list[str]],
    ct_foodtags: dict[str, list[str]],
    inv_index: dict[str, str],
    community_notes: dict[str, list[dict]] | None = None,
) -> str:
    """Build a prompt for Claude to generate notes, augmented with CT data."""
    community_notes = community_notes or {}
    lines = []
    for e in entries:
        urgent = "URGENT — past peak or expiring" if e.get("urgent") else ""
        evolution = "EVOLUTION tasting" if e.get("evolution") else ""
        occasion = f"Occasion: {e['occasion']}" if e.get("occasion") else ""
        flags = " | ".join(filter(None, [urgent, evolution, occasion]))

        line = (
            f"Week {e['week']}: {e['vintage']} {e['name']} "
            f"({e.get('appellation', '')}) "
            f"Window: {e.get('window', 'unknown')} Score: {e.get('score', 'none')} "
            f"Badge: {e.get('badge', '')} {flags}"
        )

        # Augment with CellarTracker data if available
        iwine = find_iwine(str(e.get("vintage", "")), e.get("name", ""), inv_index)
        if iwine:
            notes = ct_notes.get(iwine, [])
            if notes:
                # Include most recent note (first in list)
                line += f"\n  Your CellarTracker note: {notes[0][:200]}"
            tags = ct_foodtags.get(iwine, [])
            if tags:
                line += f"\n  Food pairings (from CT): {', '.join(tags[:5])}"
            # Include recent community tasting notes
            cn = community_notes.get(iwine, [])
            if cn:
                quotes = []
                for n in cn[:3]:
                    score_str = f" ({n['score']} pts)" if n.get("score") else ""
                    body = (n.get("body") or "")[:150]
                    if body:
                        quotes.append(
                            f'"{body}"{score_str} — {n.get("author", "anon")}'
                        )
                if quotes:
                    line += "\n  Community notes: " + " | ".join(quotes)

        lines.append(line)

    bottle_list = "\n".join(lines)
    return f"""Generate a one-sentence tasting note for each wine below. The note should:
- Be 1-2 sentences max, conversational tone
- If a CellarTracker note is provided, build on it — don't repeat it verbatim but reference the user's experience
- If community notes are provided, ground your note in what real tasters are saying — reference consensus or standout observations
- If food pairings from CT are provided, incorporate them as suggestions
- For urgent/past-peak wines: acknowledge the wine may be declining, suggest decanting or having a backup
- For evolution wines: mention comparing with previous/future vintages
- For holiday occasions: reference the occasion
- For wines in peak window: note what to expect (flavor profile, food pairing suggestion)
- Do NOT invent scores or facts — only reference what's provided

Return ONLY a JSON object mapping week numbers to notes, like:
{{"1": "note text", "2": "note text"}}

Wines:
{bottle_list}"""


def generate_notes(
    plan_path: str,
    notes_path: str | None = None,
    foodtags_path: str | None = None,
    community_notes_path: str | None = None,
    inventory_path: str | None = None,
) -> None:
    """Generate notes for plan entries with empty notes."""
    plan_data = json.loads(Path(plan_path).read_text())
    all_weeks = plan_data.get("allWeeks", [])

    # Load CellarTracker data if available
    ct_notes = parse_ct_notes(notes_path) if notes_path else {}
    ct_foodtags = parse_ct_foodtags(foodtags_path) if foodtags_path else {}
    community_notes = load_community_notes(community_notes_path)
    inv_index = load_inventory_index(inventory_path)

    if ct_notes:
        print(f"  Loaded {sum(len(v) for v in ct_notes.values())} CellarTracker notes")
    if ct_foodtags:
        print(
            f"  Loaded {sum(len(v) for v in ct_foodtags.values())} CellarTracker food tags"
        )
    if community_notes:
        total_cn = sum(len(v) for v in community_notes.values())
        print(
            f"  Loaded {total_cn} community notes across {len(community_notes)} wines"
        )

    # Find entries that need notes
    needs_notes = [w for w in all_weeks if not w.get("note")]
    if not needs_notes:
        print("All entries already have notes.")
        return

    print(f"Generating notes for {len(needs_notes)} entries...")

    # Process in batches
    week_map = {w["week"]: w for w in all_weeks}
    notes_generated = 0
    for i in range(0, len(needs_notes), BATCH_SIZE):
        batch = needs_notes[i : i + BATCH_SIZE]
        prompt = build_prompt(batch, ct_notes, ct_foodtags, inv_index, community_notes)

        print(f"  Batch {i // BATCH_SIZE + 1}: {len(batch)} wines...")
        response = call_claude(prompt)
        if not response:
            print("  WARNING: Empty response for batch, skipping")
            continue

        notes = extract_json(response)
        for week_str, note in notes.items():
            try:
                week_num = int(week_str)
            except ValueError:
                print(
                    f"WARNING: Skipping non-numeric week key: {week_str!r}",
                    file=sys.stderr,
                )
                continue
            if week_num not in week_map:
                continue
            checked = valid_note(note)
            if checked is None:
                print(
                    f"WARNING: Skipping the note for week {week_num}: "
                    f"not a string of 1-{MAX_NOTE_CHARS} characters",
                    file=sys.stderr,
                )
                continue
            week_map[week_num]["note"] = checked
            notes_generated += 1

    Path(plan_path).write_text(
        json.dumps(plan_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Generated {notes_generated} notes.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "Usage: generate_notes.py <plan.json> [notes.tsv] [foodtags.tsv] [community_notes.json] [inventory.json]",
            file=sys.stderr,
        )
        sys.exit(1)
    generate_notes(
        sys.argv[1],
        sys.argv[2] if len(sys.argv) > 2 else None,
        sys.argv[3] if len(sys.argv) > 3 else None,
        sys.argv[4] if len(sys.argv) > 4 else None,
        sys.argv[5] if len(sys.argv) > 5 else None,
    )
