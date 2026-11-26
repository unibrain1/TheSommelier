#!/usr/bin/env python3
"""LLM-based menu enrichment for richer wine-food pairing.

For each menu entry, extracts structured food features using Claude.
Caches results keyed by raw text hash so unchanged entries aren't re-enriched.
A cache value of {"not_enriched_on": "YYYY-MM-DD"} is a marker. It means that
Claude answered for the meal but gave no usable field. The marker stops a new
Claude call for that meal on the marker date and the day after it. It expires
MARKER_MAX_AGE_DAYS calendar days after the marker date, and then the meal goes
to Claude again.

Usage: enrich_menu.py <menu.json> [cache.json] [output.json]

Exit codes:
  0  every entry that needed enrichment was enriched, or all were cached
  1  no entry that needed enrichment was enriched, a file write failed,
     a missing argument, or a crash
  3  some, but not all, entries that needed enrichment were enriched
"""

import hashlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import NamedTuple

import settings
from wine_utils import call_claude, extract_json

ENRICHED_FIELDS = [
    "protein",
    "cut",
    "preparation",
    "sauce",
    "sauce_intensity",
    "sides",
    "cuisine",
    "richness",
    "acidity",
    "sweetness",
    "spice_heat",
    "dominant_flavor_axis",
    "pairing_priorities",
]

# Fields documented as scalar strings in the enrichment schema.
# Claude occasionally returns a list for these. _validate_enriched keeps the first string.
SCALAR_FIELDS: frozenset[str] = frozenset(
    {
        "protein",
        "cut",
        "preparation",
        "sauce",
        "sauce_intensity",
        "cuisine",
        "richness",
        "acidity",
        "sweetness",
        "spice_heat",
    }
)
LIST_FIELDS: frozenset[str] = frozenset(ENRICHED_FIELDS) - SCALAR_FIELDS


class EnrichResult(NamedTuple):
    """The enriched entries, the counts, and whether every file write worked."""

    results: list[dict]
    needed: int
    enriched: int
    written: bool


# The values that build_enrichment_prompt asks for.
ENUM_VALUES: dict[str, frozenset[str]] = {
    "sauce_intensity": frozenset({"light", "medium", "heavy"}),
    "richness": frozenset({"light", "medium", "rich"}),
    "acidity": frozenset({"low", "medium", "medium-high", "high"}),
    "sweetness": frozenset({"low", "medium", "high"}),
    "spice_heat": frozenset({"none", "low", "medium", "high"}),
}
# Text longer than MAX_FIELD_CHARS drops the field. A list longer than
# MAX_LIST_ITEMS is cut.
MAX_FIELD_CHARS = 80
MAX_LIST_ITEMS = 5

# A marker is the cache value for a meal that Claude answered with no usable
# field. It expires, so that a meal that comes back on the menu gets a new try.
MARKER_KEY = "not_enriched_on"
MARKER_MAX_AGE_DAYS = 2


def text_hash(text: str) -> str:
    """Stable hash of menu text for cache keying."""
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()[:16]


def build_enrichment_prompt(entries: list[dict]) -> str:
    """Build a prompt for Claude to extract structured food features."""
    items = []
    for i, entry in enumerate(entries):
        items.append(f'{i}: "{entry["meal"]}"')

    items_text = "\n".join(items)
    return f"""Extract structured food features from each menu entry below.

For each entry, produce a JSON object with these fields (omit any that don't apply):
- protein: the main protein (e.g., "pork", "chicken", "salmon")
- cut: specific cut if mentioned (e.g., "tenderloin", "thigh", "ribeye")
- preparation: cooking method (e.g., "grilled", "braised", "roasted", "fried")
- sauce: sauce description if present (e.g., "mustard pan sauce", "red wine reduction")
- sauce_intensity: "light", "medium", or "heavy"
- sides: array of side dishes (e.g., ["fingerlings", "kale"])
- cuisine: cuisine type (e.g., "american", "italian", "thai", "mexican")
- richness: overall dish richness — "light", "medium", or "rich"
- acidity: "low", "medium", "medium-high", or "high"
- sweetness: "low", "medium", or "high"
- spice_heat: "none", "low", "medium", or "high"
- dominant_flavor_axis: array of 1-3 dominant flavors (e.g., ["savory", "tangy"], ["sweet", "smoky"])
- pairing_priorities: array of 1-3 wine pairing considerations (e.g., ["match acidity to mustard", "handle grilled char"])

Rules:
- Omit fields rather than guess — only include what's clearly implied by the menu text
- For simple entries like "Leftovers" or "Pizza night", include what you can infer
- Be concise in string values
- Scalar fields (protein, cut, preparation, sauce, sauce_intensity, cuisine, richness, acidity, sweetness, spice_heat) must be a single string, not an array

Return ONLY a JSON object mapping entry index to its features, like:
{{"0": {{"protein": "pork", "preparation": "grilled", ...}}, "1": {{...}}}}

Menu entries:
{items_text}"""


def _is_marker(entry: object) -> bool:
    """Return True if the cache entry is a not-enriched marker."""
    return isinstance(entry, dict) and len(entry) == 1 and MARKER_KEY in entry


def _marker_is_fresh(entry: dict, today: date) -> bool:
    """Return True if the marker date is valid and less than MARKER_MAX_AGE_DAYS old.

    The age is in calendar days, so a marker written late on day D expires on
    day D + MARKER_MAX_AGE_DAYS like one written early. A date that does not
    parse, or a date after today, counts as expired.
    """
    value = entry.get(MARKER_KEY)
    if not isinstance(value, str):
        return False
    try:
        marked = date.fromisoformat(value)
    except ValueError:
        return False
    return 0 <= (today - marked).days < MARKER_MAX_AGE_DAYS


def load_cache(cache_path: Path, today: date | None = None) -> dict[str, dict]:
    """Load enrichment cache (keyed by text hash), with each entry checked again.

    A fresh marker stays in the cache, so its meal gets no Claude call. An
    expired or bad marker is dropped, so its meal goes to Claude again. The
    marker check comes first, because _validate_enriched drops the unknown
    marker key.

    An older release cached LLM output with no check. Each other entry is
    checked, also one for a meal that is not on the menu now, because
    _write_files writes the whole cache back. An entry with no usable field is
    dropped, so its meal is enriched again.

    today is the date of the run. Tests set it. The default is date.today().
    """
    today = today or date.today()
    if not (cache_path.exists() and cache_path.stat().st_size > 0):
        return {}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        _warn(f"cache could not be loaded: {e}")
        return {}
    if not isinstance(data, dict):
        _warn(f"cache was {type(data).__name__}, not an object — cache ignored")
        return {}
    cache: dict[str, dict] = {}
    for h, entry in data.items():
        if _is_marker(entry):
            if _marker_is_fresh(entry, today):
                cache[h] = entry
            continue
        checked = _validate_enriched(entry)
        if checked is not None:
            cache[h] = checked
    return cache


def _warn(message: str) -> None:
    print(f"WARNING: enrichment {message}", file=sys.stderr)


def _validate_scalar(field: str, val: object) -> str | None:
    """Return the usable string for a scalar field, or None to drop the field."""
    if isinstance(val, list):
        val = next((v for v in val if isinstance(v, str)), None)
        if val is None:
            _warn(f"field '{field}' had no string elements — field omitted")
            return None
        _warn(f"field '{field}' was a list — coerced to scalar")
    elif isinstance(val, (int, float, bool)):
        _warn(f"field '{field}' was {type(val).__name__} — coerced to string")
        val = str(val)
    elif not isinstance(val, str):
        _warn(f"field '{field}' was {type(val).__name__} — field omitted")
        return None
    val = val.strip()
    if not val or len(val) > MAX_FIELD_CHARS:
        _warn(f"field '{field}' was empty or too long — field omitted")
        return None
    if field in ENUM_VALUES:
        val = val.lower()
        if val not in ENUM_VALUES[field]:
            _warn(f"field '{field}' was not an allowed value — field omitted")
            return None
    return val


def _validate_list(field: str, val: object) -> list[str] | None:
    """Return the usable strings for a list field, or None to drop the field."""
    if not isinstance(val, list):
        _warn(f"field '{field}' was {type(val).__name__}, not a list — field omitted")
        return None
    items = [v.strip() for v in val if isinstance(v, str)]
    items = [v for v in items if v and len(v) <= MAX_FIELD_CHARS]
    if len(items) < len(val):
        _warn(f"field '{field}' had items that are not short strings — items omitted")
    if len(items) > MAX_LIST_ITEMS:
        _warn(f"field '{field}' had more than {MAX_LIST_ITEMS} items — list cut")
        items = items[:MAX_LIST_ITEMS]
    if not items:
        _warn(f"field '{field}' had no usable items — field omitted")
        return None
    return items


def _validate_enriched(enriched: object) -> dict | None:
    """Return a checked copy of one entry's LLM features, or None if nothing is usable.

    The LLM output comes from untrusted menu text, so each field is checked for
    type, length and allowed values before it goes into the cache or to pairing.
    A bad field is dropped. A warning names the field, never a value. An
    unknown key is LLM text, so its warning shows at most 40 characters of it.
    """
    if not isinstance(enriched, dict):
        _warn(f"entry was {type(enriched).__name__}, not an object — entry omitted")
        return None
    result: dict = {}
    for field, val in enriched.items():
        if field in SCALAR_FIELDS:
            checked = _validate_scalar(field, val)
        elif field in LIST_FIELDS:
            checked = _validate_list(field, val)
        else:
            # The key is LLM text: repr and a length limit keep the log line safe.
            _warn(f"unknown field {repr(field)[:40]} — field omitted")
            continue
        if checked is not None:
            result[field] = checked
    return result or None


def enrich_menu(
    menu_path: Path,
    cache_path: Path,
    output_path: Path | None = None,
    today: date | None = None,
) -> EnrichResult:
    """Enrich menu entries with LLM-extracted food features.

    Return the entries, how many needed enrichment, how many got it, and
    whether the files were written. A meal with a marker in the cache gets
    "enriched": None. today is the date of the run, and the default is
    date.today().
    """
    today = today or date.today()
    menu = json.loads(menu_path.read_text(encoding="utf-8"))
    cache = load_cache(cache_path, today)

    # Separate cached vs uncached entries
    results = []
    to_enrich = []  # (index_in_results, entry)

    for entry in menu:
        h = text_hash(entry["meal"])
        if h in cache:
            # The marker stays in the cache file only, so that pairing and
            # menu_enriched_full.json see one form for "not enriched".
            enriched = None if _is_marker(cache[h]) else cache[h]
            results.append(
                {"raw": entry["meal"], "hash": h, "enriched": enriched, **entry}
            )
        else:
            results.append({"raw": entry["meal"], "hash": h, "enriched": None, **entry})
            to_enrich.append((len(results) - 1, entry))

    # A marked meal is a cache hit with no enrichment. The count keeps the
    # log line from saying that every meal is enriched.
    marked = sum(1 for r in results if r["hash"] in cache and r["enriched"] is None)
    marked_note = f" ({marked} marked as not enriched)" if marked else ""

    if not to_enrich:
        print(
            f"Menu enrichment: all {len(menu)} entries cached{marked_note}, "
            "nothing to enrich."
        )
        written = _write_files(results, cache, cache_path, output_path)
        return EnrichResult(results, needed=0, enriched=0, written=written)

    cache_hits = len(menu) - len(to_enrich)
    print(
        f"Menu enrichment: {cache_hits} cached{marked_note}, "
        f"{len(to_enrich)} to enrich..."
    )

    # Batch all uncached entries in one Claude call
    entries_to_enrich = [e for _, e in to_enrich]
    prompt = build_enrichment_prompt(entries_to_enrich)
    response = call_claude(prompt)

    if not response:
        print(
            "WARNING: Empty response from Claude — all entries unenriched",
            file=sys.stderr,
        )
        written = _write_files(results, cache, cache_path, output_path)
        return EnrichResult(results, needed=len(to_enrich), enriched=0, written=written)

    enrichments = extract_json(response)

    # Map enrichments back to results
    enriched_count = 0
    for batch_idx, (result_idx, entry) in enumerate(to_enrich):
        h = results[result_idx]["hash"]
        raw = enrichments.get(str(batch_idx))
        if raw is None:
            # Claude gave no answer for this meal, so a later run can try again.
            _warn(f"entry {h} missing from the reply — not cached")
            continue
        enriched = _validate_enriched(raw)
        if enriched is None:
            # Claude answered, but nothing was usable. The marker stops the
            # same Claude call on each run until it expires.
            retry = today + timedelta(days=MARKER_MAX_AGE_DAYS)
            _warn(
                f"entry {h} had no usable field — cached as not enriched "
                f"until {retry.isoformat()}"
            )
            cache[h] = {MARKER_KEY: today.isoformat()}
            continue
        results[result_idx]["enriched"] = enriched
        cache[h] = enriched
        enriched_count += 1

    print(f"  Enriched {enriched_count}/{len(to_enrich)} entries.")

    written = _write_files(results, cache, cache_path, output_path)
    return EnrichResult(
        results, needed=len(to_enrich), enriched=enriched_count, written=written
    )


def _write_files(
    results: list[dict],
    cache: dict[str, dict],
    cache_path: Path,
    output_path: Path | None = None,
) -> bool:
    """Write cache and full results atomically. Return False if a write failed."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    files: list[tuple[Path, list | dict]] = [(cache_path, cache)]
    if output_path:
        files.append((output_path, results))
    written = True
    for path, data in files:
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            tmp.replace(path)
        except OSError as e:
            print(f"ERROR: Failed to write {path}: {e}", file=sys.stderr)
            tmp.unlink(missing_ok=True)
            written = False
    return written


def main(argv: list[str]) -> int:
    """Run the script and return its exit code (see the module docstring)."""
    if len(argv) < 2:
        print(
            "Usage: enrich_menu.py <menu.json> [cache.json] [output.json]",
            file=sys.stderr,
        )
        return 1

    menu_path = Path(argv[1])
    cache_path = (
        Path(argv[2]) if len(argv) > 2 else settings.data_dir() / "menu_enriched.json"
    )
    output_path = (
        Path(argv[3])
        if len(argv) > 3
        else settings.data_dir() / "menu_enriched_full.json"
    )

    result = enrich_menu(menu_path, cache_path, output_path)
    if not result.written:
        return 1
    if result.enriched == result.needed:
        return 0
    if result.enriched == 0:
        print(
            f"ERROR: No menu entries enriched of {result.needed}",
            file=sys.stderr,
        )
        return 1
    # Exit 3 is a partial result. pipeline.sh treats each other non-zero code
    # as a failed enrichment step.
    print(
        f"WARNING: {result.needed - result.enriched} of {result.needed} "
        "menu entries not enriched",
        file=sys.stderr,
    )
    return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv))
