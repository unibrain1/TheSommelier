#!/usr/bin/env python3
"""LLM-based menu enrichment for richer wine-food pairing.

For each menu entry, extracts structured food features using Claude.
Caches results keyed by raw text hash so unchanged entries aren't re-enriched.

Usage: enrich_menu.py <menu.json> [cache.json] [output.json]
"""

import hashlib
import json
import sys
from pathlib import Path

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
# The values that build_enrichment_prompt asks for.
ENUM_VALUES: dict[str, frozenset[str]] = {
    "sauce_intensity": frozenset({"light", "medium", "heavy"}),
    "richness": frozenset({"light", "medium", "rich"}),
    "acidity": frozenset({"low", "medium", "medium-high", "high"}),
    "sweetness": frozenset({"low", "medium", "high"}),
    "spice_heat": frozenset({"none", "low", "medium", "high"}),
}
MAX_FIELD_CHARS = 80
MAX_LIST_ITEMS = 5


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


def load_cache(cache_path: Path) -> dict[str, dict]:
    """Load enrichment cache (keyed by text hash), with each entry checked again.

    An older release cached LLM output with no check. Each entry is checked,
    also one for a meal that is not on the menu now, because _write_files
    writes the whole cache back. An entry with no usable field is dropped, so
    its meal is enriched again.
    """
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
    checked = {h: _validate_enriched(entry) for h, entry in data.items()}
    return {h: entry for h, entry in checked.items() if entry is not None}


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
    A bad field is dropped. A warning names the field, never a value.
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
    menu_path: Path, cache_path: Path, output_path: Path | None = None
) -> list[dict]:
    """Enrich menu entries with LLM-extracted food features."""
    menu = json.loads(menu_path.read_text(encoding="utf-8"))
    cache = load_cache(cache_path)

    # Separate cached vs uncached entries
    results = []
    to_enrich = []  # (index_in_results, entry)

    for entry in menu:
        h = text_hash(entry["meal"])
        if h in cache:
            results.append(
                {"raw": entry["meal"], "hash": h, "enriched": cache[h], **entry}
            )
        else:
            results.append({"raw": entry["meal"], "hash": h, "enriched": None, **entry})
            to_enrich.append((len(results) - 1, entry))

    if not to_enrich:
        print(f"Menu enrichment: all {len(menu)} entries cached, nothing to enrich.")
        _write_files(results, cache, cache_path, output_path)
        return results

    cache_hits = len(menu) - len(to_enrich)
    print(f"Menu enrichment: {cache_hits} cached, {len(to_enrich)} to enrich...")

    # Batch all uncached entries in one Claude call
    entries_to_enrich = [e for _, e in to_enrich]
    prompt = build_enrichment_prompt(entries_to_enrich)
    response = call_claude(prompt)

    if not response:
        print("WARNING: Empty response from Claude — all entries unenriched")
        _write_files(results, cache, cache_path, output_path)
        return results

    enrichments = extract_json(response)

    # Map enrichments back to results
    enriched_count = 0
    for batch_idx, (result_idx, entry) in enumerate(to_enrich):
        raw = enrichments.get(str(batch_idx))
        if raw is None:
            continue
        enriched = _validate_enriched(raw)
        if enriched is not None:
            h = results[result_idx]["hash"]
            results[result_idx]["enriched"] = enriched
            cache[h] = enriched
            enriched_count += 1

    print(f"  Enriched {enriched_count}/{len(to_enrich)} entries.")

    _write_files(results, cache, cache_path, output_path)
    return results


def _write_files(
    results: list[dict],
    cache: dict[str, dict],
    cache_path: Path,
    output_path: Path | None = None,
) -> None:
    """Write cache and full results atomically."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    files: list[tuple[Path, list | dict]] = [(cache_path, cache)]
    if output_path:
        files.append((output_path, results))
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


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "Usage: enrich_menu.py <menu.json> [cache.json] [output.json]",
            file=sys.stderr,
        )
        sys.exit(1)

    menu_path = Path(sys.argv[1])
    cache_path = (
        Path(sys.argv[2])
        if len(sys.argv) > 2
        else settings.data_dir() / "menu_enriched.json"
    )
    output_path = (
        Path(sys.argv[3])
        if len(sys.argv) > 3
        else settings.data_dir() / "menu_enriched_full.json"
    )

    enrich_menu(menu_path, cache_path, output_path)
