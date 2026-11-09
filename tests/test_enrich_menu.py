"""Tests for enrich_menu.py — prompt building, JSON extraction, cache."""

import json
from unittest.mock import patch

from enrich_menu import (
    _validate_enriched,
    build_enrichment_prompt,
    enrich_menu,
    load_cache,
    text_hash,
)
from wine_utils import extract_json


class TestTextHash:
    def test_deterministic(self):
        assert text_hash("Grilled chicken") == text_hash("Grilled chicken")

    def test_case_insensitive(self):
        assert text_hash("Grilled Chicken") == text_hash("grilled chicken")

    def test_strips_whitespace(self):
        assert text_hash("  chicken  ") == text_hash("chicken")

    def test_different_text_different_hash(self):
        assert text_hash("chicken") != text_hash("beef")

    def test_returns_16_chars(self):
        assert len(text_hash("anything")) == 16


class TestBuildEnrichmentPrompt:
    def test_includes_all_entries(self):
        entries = [
            {"meal": "Grilled chicken"},
            {"meal": "Pasta primavera"},
        ]
        prompt = build_enrichment_prompt(entries)
        assert "Grilled chicken" in prompt
        assert "Pasta primavera" in prompt

    def test_entries_are_indexed(self):
        entries = [{"meal": "Steak"}, {"meal": "Fish"}]
        prompt = build_enrichment_prompt(entries)
        assert '0: "Steak"' in prompt
        assert '1: "Fish"' in prompt

    def test_mentions_required_fields(self):
        prompt = build_enrichment_prompt([{"meal": "test"}])
        assert "protein" in prompt
        assert "preparation" in prompt
        assert "richness" in prompt
        assert "spice_heat" in prompt
        assert "pairing_priorities" in prompt


class TestExtractJson:
    def test_extracts_json_from_text(self):
        text = 'Here is the result: {"0": {"protein": "chicken"}} done.'
        result = extract_json(text)
        assert result == {"0": {"protein": "chicken"}}

    def test_handles_no_json(self):
        assert extract_json("no json here") == {}

    def test_handles_invalid_json(self):
        assert extract_json("{invalid: json}") == {}

    def test_handles_nested_json(self):
        text = '{"0": {"protein": "pork", "sides": ["kale", "potatoes"]}}'
        result = extract_json(text)
        assert result["0"]["sides"] == ["kale", "potatoes"]


class TestLoadCache:
    def test_missing_file_returns_empty(self, tmp_path):
        assert load_cache(tmp_path / "nonexistent.json") == {}

    def test_empty_file_returns_empty(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text("")
        assert load_cache(p) == {}

    def test_valid_cache(self, tmp_path):
        p = tmp_path / "cache.json"
        data = {"abc123": {"protein": "chicken"}}
        p.write_text(json.dumps(data))
        assert load_cache(p) == data

    def test_invalid_json_returns_empty(self, tmp_path, capsys):
        p = tmp_path / "bad.json"
        p.write_text("{bad")
        assert load_cache(p) == {}
        assert (
            "WARNING: enrichment cache could not be loaded" in capsys.readouterr().err
        )

    def test_cache_that_is_not_utf8_returns_empty(self, tmp_path, capsys):
        p = tmp_path / "bad.json"
        p.write_bytes(b"\xff\xfe{}")
        assert load_cache(p) == {}
        assert (
            "WARNING: enrichment cache could not be loaded" in capsys.readouterr().err
        )


class TestValidateEnriched:
    """Unit tests for _validate_enriched — the check of LLM output before caching."""

    def test_scalar_list_coerced_to_first_string(self):
        result = _validate_enriched({"protein": ["chicken", "shrimp"]})
        assert result["protein"] == "chicken"

    def test_scalar_single_element_list_coerced(self):
        result = _validate_enriched({"richness": ["medium"]})
        assert result["richness"] == "medium"

    def test_all_ten_scalar_fields_coerced(self):
        # Each enum field needs an allowed value, so each field gets its own.
        payload_values = {
            "protein": "x",
            "cut": "x",
            "preparation": "x",
            "sauce": "x",
            "sauce_intensity": "light",
            "cuisine": "x",
            "richness": "rich",
            "acidity": "medium-high",
            "sweetness": "low",
            "spice_heat": "none",
        }
        payload = {f: [v] for f, v in payload_values.items()}
        result = _validate_enriched(payload)
        for field, value in payload_values.items():
            assert result[field] == value, (
                f"Expected '{field}' to be coerced to {value!r}, got {result[field]!r}"
            )

    def test_scalar_already_string_unchanged(self):
        result = _validate_enriched({"protein": "beef"})
        assert result["protein"] == "beef"

    def test_scalar_int_coerced_to_string(self):
        result = _validate_enriched({"protein": 3})
        assert result["protein"] == "3"

    def test_scalar_float_coerced_to_string(self):
        result = _validate_enriched({"cut": 0.7})
        assert result["cut"] == "0.7"

    def test_scalar_bool_coerced_to_string(self):
        result = _validate_enriched({"sauce": True})
        assert result["sauce"] == "True"

    def test_scalar_none_dropped(self):
        result = _validate_enriched({"protein": None, "cut": "loin"})
        assert result == {"cut": "loin"}

    def test_scalar_dict_dropped(self):
        result = _validate_enriched({"protein": {"name": "pork"}, "cut": "loin"})
        assert result == {"cut": "loin"}

    def test_scalar_empty_list_field_omitted(self):
        result = _validate_enriched({"protein": [], "cut": "loin"})
        assert "protein" not in result

    def test_scalar_list_no_string_elements_field_omitted(self):
        # A list of ints contains no string elements, so the field should be omitted.
        result = _validate_enriched({"protein": [42, 99], "cut": "loin"})
        assert "protein" not in result

    def test_array_field_sides_untouched(self):
        result = _validate_enriched({"sides": ["potatoes", "kale"]})
        assert result["sides"] == ["potatoes", "kale"]

    def test_array_field_dominant_flavor_axis_untouched(self):
        # Single-element list on an array field must NOT be coerced to a bare string.
        result = _validate_enriched({"dominant_flavor_axis": ["savory"]})
        assert result["dominant_flavor_axis"] == ["savory"]

    def test_array_field_pairing_priorities_untouched(self):
        result = _validate_enriched({"pairing_priorities": ["match acidity"]})
        assert result["pairing_priorities"] == ["match acidity"]

    def test_array_single_element_not_coerced(self):
        # Critical boundary: single-element array fields must never be coerced to strings.
        result = _validate_enriched({"sides": ["potatoes"]})
        assert result["sides"] == ["potatoes"]

    def test_array_field_string_dropped(self):
        result = _validate_enriched({"sides": "potatoes", "cut": "loin"})
        assert result == {"cut": "loin"}

    def test_mixed_realistic_payload(self):
        payload = {
            "protein": ["pork"],
            "preparation": ["grilled"],
            "richness": "medium",
            "sides": ["fingerlings", "kale"],
            "dominant_flavor_axis": ["savory", "smoky"],
            "pairing_priorities": ["match weight"],
        }
        result = _validate_enriched(payload)
        assert result["protein"] == "pork"
        assert result["preparation"] == "grilled"
        assert result["richness"] == "medium"
        assert result["sides"] == ["fingerlings", "kale"]
        assert result["dominant_flavor_axis"] == ["savory", "smoky"]
        assert result["pairing_priorities"] == ["match weight"]

    def test_empty_dict_returns_none(self):
        assert _validate_enriched({}) is None

    def test_returns_new_dict_not_mutates_input(self):
        original = {"protein": ["beef"]}
        _validate_enriched(original)
        assert original["protein"] == ["beef"]

    def test_not_a_dict_returns_none(self, capsys):
        assert _validate_enriched(["pork", "grilled"]) is None
        assert "WARNING" in capsys.readouterr().err

    def test_unknown_key_dropped(self, capsys):
        result = _validate_enriched({"protein": "pork", "instructions": "ignore all"})
        assert result == {"protein": "pork"}
        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "ignore all" not in err

    def test_scalar_too_long_dropped(self, capsys):
        result = _validate_enriched({"protein": "p" * 81, "cut": "c" * 80})
        assert result == {"cut": "c" * 80}
        err = capsys.readouterr().err
        assert "WARNING" in err and "'protein'" in err
        assert "p" * 81 not in err

    def test_empty_string_dropped(self):
        result = _validate_enriched({"protein": "   ", "cut": " loin "})
        assert result == {"cut": "loin"}

    def test_enum_value_not_in_set_dropped(self, capsys):
        result = _validate_enriched({"richness": "extreme", "acidity": "HIGH"})
        assert result == {"acidity": "high"}
        err = capsys.readouterr().err
        assert "'richness'" in err
        assert "extreme" not in err

    def test_list_with_non_string_keeps_only_strings(self):
        result = _validate_enriched({"sides": ["kale", 3, None, {"x": 1}, " rice "]})
        assert result == {"sides": ["kale", "rice"]}

    def test_list_over_five_items_cut_to_five(self):
        result = _validate_enriched({"sides": [f"side{i}" for i in range(8)]})
        assert result == {"sides": [f"side{i}" for i in range(5)]}

    def test_all_fields_bad_returns_none(self):
        payload = {
            "protein": "",
            "richness": "extreme",
            "sides": [1, 2],
            "cuisine": None,
            "unknown": "x",
        }
        assert _validate_enriched(payload) is None


class TestEnrichMenuRepairsCache:
    """Integration test: enrich_menu() checks each cached entry and repairs the file."""

    def test_bad_cached_entry_repaired_in_file(self, tmp_path):
        menu_path = tmp_path / "menu.json"
        cache_path = tmp_path / "cache.json"
        good_text = "Grilled pork chop"
        bad_text = "Mystery dish"
        menu_path.write_text(
            json.dumps(
                [
                    {"date": "2026-06-01", "meal": good_text},
                    {"date": "2026-06-02", "meal": bad_text},
                ]
            ),
            encoding="utf-8",
        )
        cache_path.write_text(
            json.dumps(
                {
                    text_hash(good_text): {
                        "protein": "pork",
                        "richness": "EXTREME",
                        "injected": "<script>",
                        "sides": ["s"] * 9,
                    },
                    text_hash(bad_text): {"richness": "extreme"},
                }
            ),
            encoding="utf-8",
        )

        with patch("enrich_menu.call_claude", return_value="") as mock_claude:
            results = enrich_menu(menu_path, cache_path)
        # The entry with no usable field is enriched again, the repaired one is not.
        mock_claude.assert_called_once()
        assert bad_text in mock_claude.call_args.args[0]
        assert good_text not in mock_claude.call_args.args[0]

        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        assert cache == {text_hash(good_text): {"protein": "pork", "sides": ["s"] * 5}}
        assert results[1]["enriched"] is None

    def test_cached_entry_of_an_old_meal_is_checked(self, tmp_path):
        menu_path = tmp_path / "menu.json"
        cache_path = tmp_path / "cache.json"
        menu_path.write_text("[]", encoding="utf-8")
        cache_path.write_text(
            json.dumps(
                {
                    "old-good": {"protein": "beef", "injected": "x"},
                    "old-bad": "not an object",
                }
            ),
            encoding="utf-8",
        )

        with patch("enrich_menu.call_claude") as mock_claude:
            enrich_menu(menu_path, cache_path)
        mock_claude.assert_not_called()

        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        assert cache == {"old-good": {"protein": "beef"}}

    def test_cache_that_is_not_an_object_is_ignored(self, tmp_path, capsys):
        cache_path = tmp_path / "cache.json"
        cache_path.write_text('["SECRET-a", "b"]', encoding="utf-8")

        assert load_cache(cache_path) == {}
        err = capsys.readouterr().err
        assert "WARNING: enrichment cache was list" in err
        assert "SECRET-a" not in err

    def test_bad_reply_from_claude_is_not_cached(self, tmp_path):
        menu_path = tmp_path / "menu.json"
        cache_path = tmp_path / "cache.json"
        menu_path.write_text(
            json.dumps(
                [
                    {"date": "2026-06-01", "meal": "Pork chop"},
                    {"date": "2026-06-02", "meal": "Salmon"},
                ]
            ),
            encoding="utf-8",
        )
        reply = json.dumps({"0": "pork", "1": {"richness": "extreme"}})

        with patch("enrich_menu.call_claude", return_value=reply):
            results = enrich_menu(menu_path, cache_path)

        assert json.loads(cache_path.read_text(encoding="utf-8")) == {}
        assert [r["enriched"] for r in results] == [None, None]


class TestEnrichMenuNormalizesBeforeCache:
    """Integration test: enrich_menu() applies _validate_enriched before writing the cache."""

    def test_normalized_values_written_to_cache(self, tmp_path):
        menu_path = tmp_path / "menu.json"
        cache_path = tmp_path / "cache.json"

        menu_entry_text = "Grilled pork chop with potatoes"
        menu_path.write_text(
            json.dumps([{"date": "2026-06-01", "meal": menu_entry_text}]),
            encoding="utf-8",
        )

        # Simulate Claude returning a scalar field (protein) as a list, and an array field intact.
        mock_response = json.dumps(
            {
                "0": {
                    "protein": ["pork"],
                    "preparation": "grilled",
                    "sides": ["potatoes", "kale"],
                }
            }
        )

        with patch("enrich_menu.call_claude", return_value=mock_response):
            enrich_menu(menu_path, cache_path)

        assert cache_path.exists(), "Cache file was not written"
        cache = json.loads(cache_path.read_text(encoding="utf-8"))

        # Locate the cache entry by hashing the menu text the same way the module does.
        entry_hash = text_hash(menu_entry_text)
        assert entry_hash in cache, (
            f"Hash {entry_hash!r} not found in cache; keys: {list(cache)}"
        )

        entry = cache[entry_hash]
        assert entry["protein"] == "pork", (
            f"Expected protein coerced to 'pork' (string), got {entry['protein']!r}"
        )
        assert entry["preparation"] == "grilled", (
            f"Expected preparation 'grilled' unchanged, got {entry['preparation']!r}"
        )
        assert entry["sides"] == ["potatoes", "kale"], (
            f"Expected sides array preserved, got {entry['sides']!r}"
        )
