"""Tests for enrich_menu.py — prompt building, JSON extraction, cache."""

import json
from datetime import date, timedelta
from unittest.mock import patch


from enrich_menu import (
    MARKER_KEY,
    _validate_enriched,
    build_enrichment_prompt,
    enrich_menu,
    load_cache,
    main,
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
            results = enrich_menu(menu_path, cache_path).results
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

    def test_bad_reply_from_claude_is_cached_as_marker(self, tmp_path):
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

        today = date(2026, 6, 1)

        with patch("enrich_menu.call_claude", return_value=reply):
            results = enrich_menu(menu_path, cache_path, today=today).results

        # Claude answered for both meals, but gave nothing usable.
        marker = {MARKER_KEY: "2026-06-01"}
        assert json.loads(cache_path.read_text(encoding="utf-8")) == {
            text_hash("Pork chop"): marker,
            text_hash("Salmon"): marker,
        }
        assert [r["enriched"] for r in results] == [None, None]


TODAY = date(2026, 6, 10)


def _marker(days_old: int) -> dict:
    return {MARKER_KEY: (TODAY - timedelta(days=days_old)).isoformat()}


class TestLoadCacheMarker:
    """load_cache keeps a fresh marker and drops an expired or bad one."""

    def _load(self, tmp_path, entry):
        p = tmp_path / "cache.json"
        p.write_text(json.dumps({"h": entry}), encoding="utf-8")
        return load_cache(p, today=TODAY)

    def test_marker_from_today_kept(self, tmp_path):
        assert self._load(tmp_path, _marker(0)) == {"h": _marker(0)}

    def test_marker_1_day_old_kept(self, tmp_path):
        assert self._load(tmp_path, _marker(1)) == {"h": _marker(1)}

    def test_marker_2_days_old_dropped(self, tmp_path):
        assert self._load(tmp_path, _marker(2)) == {}

    def test_marker_with_bad_date_dropped(self, tmp_path):
        assert self._load(tmp_path, {MARKER_KEY: "not a date"}) == {}

    def test_marker_with_date_that_is_not_text_dropped(self, tmp_path):
        assert self._load(tmp_path, {MARKER_KEY: 20260610}) == {}

    def test_marker_with_future_date_dropped(self, tmp_path):
        assert self._load(tmp_path, _marker(-1)) == {}

    def test_entry_with_no_usable_field_still_dropped(self, tmp_path):
        assert self._load(tmp_path, {"richness": "extreme"}) == {}


class TestEnrichMenuMarker:
    """A meal that Claude answered with no usable field gets a dated marker."""

    def _run(self, tmp_path, reply, today=TODAY, meals=("Pork chop",)):
        menu_path = tmp_path / "menu.json"
        _write_menu(menu_path, list(meals))
        cache_path = tmp_path / "cache.json"
        with patch("enrich_menu.call_claude", return_value=reply) as mock_claude:
            result = enrich_menu(menu_path, cache_path, today=today)
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        return result, cache, mock_claude

    def test_no_usable_field_writes_marker_and_one_line(self, tmp_path, capsys):
        reply = json.dumps({"0": {"richness": "extreme"}})
        result, cache, _ = self._run(tmp_path, reply)
        h = text_hash("Pork chop")
        assert cache == {h: {MARKER_KEY: TODAY.isoformat()}}
        assert result.enriched == 0
        assert result.results[0]["enriched"] is None
        lines = [line for line in capsys.readouterr().err.splitlines() if h in line]
        assert lines == [
            f"WARNING: enrichment entry {h} had no usable field — "
            "cached as not enriched until 2026-06-12"
        ]
        assert "Pork" not in "\n".join(lines)

    def test_missing_index_writes_no_marker_and_one_line(self, tmp_path, capsys):
        reply = json.dumps({"0": {"protein": "pork"}})
        result, cache, _ = self._run(tmp_path, reply, meals=("Pork chop", "Salmon"))
        h = text_hash("Salmon")
        assert h not in cache
        assert cache[text_hash("Pork chop")] == {"protein": "pork"}
        assert result.enriched == 1
        lines = [line for line in capsys.readouterr().err.splitlines() if h in line]
        assert lines == [
            f"WARNING: enrichment entry {h} missing from the reply — not cached"
        ]

    def test_empty_reply_writes_no_marker(self, tmp_path):
        _, cache, _ = self._run(tmp_path, "")
        assert cache == {}

    def test_second_run_makes_no_claude_call(self, tmp_path):
        menu_path = tmp_path / "menu.json"
        _write_menu(menu_path, ["Pork chop"])
        args = [
            "enrich_menu.py",
            str(menu_path),
            str(tmp_path / "cache.json"),
            str(tmp_path / "full.json"),
        ]
        reply = json.dumps({"0": {"richness": "extreme"}})
        with (
            patch("enrich_menu.call_claude", return_value=reply) as mock_claude,
            patch("enrich_menu.date") as mock_date,
        ):
            mock_date.today.return_value = TODAY
            mock_date.fromisoformat = date.fromisoformat
            assert main(args) == 1
            assert main(args) == 0
        assert mock_claude.call_count == 1
        full = json.loads((tmp_path / "full.json").read_text(encoding="utf-8"))
        assert full[0]["enriched"] is None

    def test_marker_on_the_next_day_makes_no_call_and_is_logged(self, tmp_path, capsys):
        self._run(tmp_path, json.dumps({"0": {"richness": "extreme"}}))
        capsys.readouterr()
        result, _, mock_claude = self._run(
            tmp_path, "", today=TODAY + timedelta(days=1)
        )
        mock_claude.assert_not_called()
        assert result.needed == 0
        out = capsys.readouterr().out
        assert "all 1 entries cached (1 marked as not enriched)" in out

    def test_expired_marker_sent_to_claude_again(self, tmp_path):
        bad = json.dumps({"0": {"richness": "extreme"}})
        self._run(tmp_path, bad)
        good = json.dumps({"0": {"protein": "pork"}})
        result, cache, mock_claude = self._run(
            tmp_path, good, today=TODAY + timedelta(days=2)
        )
        mock_claude.assert_called_once()
        assert result.enriched == result.needed == 1
        assert cache == {text_hash("Pork chop"): {"protein": "pork"}}


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


def _write_menu(path, meals):
    path.write_text(
        json.dumps(
            [{"date": f"2026-06-0{i + 1}", "meal": m} for i, m in enumerate(meals)]
        ),
        encoding="utf-8",
    )


class TestMainExitCode:
    """main() returns 0, 1 or 3 so that pipeline.sh can count a warning."""

    def _run(self, tmp_path, reply):
        menu_path = tmp_path / "menu.json"
        _write_menu(menu_path, ["Pork chop", "Salmon"])
        args = [
            "enrich_menu.py",
            str(menu_path),
            str(tmp_path / "cache.json"),
            str(tmp_path / "full.json"),
        ]
        with patch("enrich_menu.call_claude", return_value=reply):
            return main(args)

    def test_all_entries_enriched_returns_0(self, tmp_path, capsys):
        reply = json.dumps({"0": {"protein": "pork"}, "1": {"protein": "salmon"}})
        assert self._run(tmp_path, reply) == 0
        assert "ERROR" not in capsys.readouterr().err

    def test_all_entries_cached_returns_0(self, tmp_path):
        menu_path = tmp_path / "menu.json"
        cache_path = tmp_path / "cache.json"
        _write_menu(menu_path, ["Pork chop"])
        cache_path.write_text(
            json.dumps({text_hash("Pork chop"): {"protein": "pork"}}), encoding="utf-8"
        )
        with patch("enrich_menu.call_claude") as mock_claude:
            assert (
                main(
                    [
                        "enrich_menu.py",
                        str(menu_path),
                        str(cache_path),
                        str(tmp_path / "full.json"),
                    ]
                )
                == 0
            )
        mock_claude.assert_not_called()

    def test_some_entries_enriched_returns_3(self, tmp_path, capsys):
        reply = json.dumps({"0": {"protein": "pork"}, "1": {"richness": "extreme"}})
        assert self._run(tmp_path, reply) == 3
        assert "WARNING: 1 of 2 menu entries not enriched" in capsys.readouterr().err

    def test_empty_reply_returns_1(self, tmp_path, capsys):
        assert self._run(tmp_path, "") == 1
        err = capsys.readouterr().err
        assert "WARNING: Empty response from Claude" in err
        assert "ERROR: No menu entries enriched of 2" in err

    def test_reply_with_no_usable_entry_returns_1(self, tmp_path, capsys):
        reply = json.dumps({"0": "pork", "1": {"richness": "extreme"}})
        assert self._run(tmp_path, reply) == 1
        assert "ERROR: No menu entries enriched of 2" in capsys.readouterr().err

    def test_write_failure_with_all_entries_cached_returns_1(self, tmp_path, capsys):
        menu_path = tmp_path / "menu.json"
        _write_menu(menu_path, ["Pork chop"])
        cache_path = tmp_path / "cache.json"
        cache_path.write_text(
            json.dumps({text_hash("Pork chop"): {"protein": "pork"}}),
            encoding="utf-8",
        )
        # needed=0, so only the write check can give exit 1. A folder at the
        # output path makes the atomic replace fail.
        output_path = tmp_path / "full.json"
        output_path.mkdir()
        with patch("enrich_menu.call_claude") as claude:
            assert (
                main(
                    [
                        "enrich_menu.py",
                        str(menu_path),
                        str(cache_path),
                        str(output_path),
                    ]
                )
                == 1
            )
        claude.assert_not_called()
        captured = capsys.readouterr()
        assert "all 1 entries cached" in captured.out
        assert "ERROR: Failed to write" in captured.err

    def test_write_failure_after_enrichment_returns_1(self, tmp_path, capsys):
        reply = json.dumps({"0": {"protein": "pork"}})
        menu_path = tmp_path / "menu.json"
        _write_menu(menu_path, ["Pork chop"])
        # A folder at the cache path makes the atomic replace fail.
        cache_path = tmp_path / "cache.json"
        cache_path.mkdir()
        with patch("enrich_menu.call_claude", return_value=reply):
            assert (
                main(
                    [
                        "enrich_menu.py",
                        str(menu_path),
                        str(cache_path),
                        str(tmp_path / "full.json"),
                    ]
                )
                == 1
            )
        assert "ERROR: Failed to write" in capsys.readouterr().err
