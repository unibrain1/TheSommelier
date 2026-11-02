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
# Claude occasionally returns a list for these; we normalize before caching.
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
# NOTE: sides, dominant_flavor_axis, pairing_priorities are intentional arrays — omit them.


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
    """Load enrichment cache (keyed by text hash)."""
    if cache_path.exists() and cache_path.stat().st_size > 0:
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"WARNING: Could not load enrichment cache: {e}", file=sys.stderr)
    return {}


def _normalize_enriched(enriched: dict) -> dict:
    """Coerce scalar fields to strings before caching.

    Claude occasionally returns a list (e.g. protein=["salmon", "shrimp"]) or a
    non-string scalar (e.g. richness=3) for fields documented as scalar strings.
    This normalizes them so bad shapes never enter the cache.

    - list  → first string element; if none, field is omitted
    - non-string scalar (int, float, bool, …) → str(val)
    - correct string → unchanged
    - array fields (sides, dominant_flavor_axis, pairing_priorities) → untouched
    """
    result = dict(enriched)  # shallow copy — never mutate the caller's dict
    for field in SCALAR_FIELDS:
        val = result.get(field)
        if val is None:
            continue
        if isinstance(val, list):
            first = next((v for v in val if isinstance(v, str)), None)
            if first is not None:
                result[field] = first
                print(
                    f"WARNING: enrichment field '{field}' was a list — coerced to scalar",
                    file=sys.stderr,
                )
            else:
                del result[field]
                print(
                    f"WARNING: enrichment field '{field}' had no string elements — field omitted",
                    file=sys.stderr,
                )
        elif not isinstance(val, str):
            result[field] = str(val)
            print(
                f"WARNING: enrichment field '{field}' was {type(val).__name__} — coerced to string",
                file=sys.stderr,
            )
    return result


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
            normalized = _normalize_enriched(cache[h])
            cache[h] = (
                normalized  # update in-memory cache so _write_files repairs the file
            )
            results.append(
                {"raw": entry["meal"], "hash": h, "enriched": normalized, **entry}
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
        enriched = enrichments.get(str(batch_idx))
        if enriched and isinstance(enriched, dict):
            enriched = _normalize_enriched(enriched)
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
