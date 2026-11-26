"""Tests for enriched pairing matching in pairing.py."""

import json
from datetime import date
from unittest.mock import patch

from enrich_menu import enrich_menu, text_hash
from pairing import _build_enriched_index, score_enriched_pairing, suggest_pairings


class TestScoreEnrichedPairing:
    """score_enriched_pairing() scores wine against enriched food features."""

    def test_good_match_bold_red_with_beef(self):
        # Cabernet should match beef + grilled + rich
        enriched = {"protein": "beef", "preparation": "grilled", "richness": "rich"}
        result = score_enriched_pairing("cabernet sauvignon reserve", enriched)
        assert result is not None
        assert result["score"] == "good"

    def test_poor_match_white_with_beef(self):
        # Sauvignon blanc should not match beef
        enriched = {"protein": "beef", "preparation": "grilled", "richness": "rich"}
        result = score_enriched_pairing("sauvignon blanc", enriched)
        assert result is not None
        assert result["score"] in ("poor", "partial")

    def test_good_match_pinot_with_chicken(self):
        enriched = {
            "protein": "chicken",
            "preparation": "roasted",
            "richness": "medium",
        }
        result = score_enriched_pairing("pinot noir laurène", enriched)
        assert result is not None
        assert result["score"] == "good"

    def test_no_signal_returns_none(self):
        # Enriched data with no recognized features
        enriched = {"sides": ["potatoes"]}
        result = score_enriched_pairing("any wine", enriched)
        assert result is None

    def test_empty_enriched_returns_none(self):
        result = score_enriched_pairing("any wine", {})
        assert result is None

    def test_confidence_high_with_many_signals(self):
        enriched = {
            "protein": "pork",
            "preparation": "grilled",
            "richness": "medium",
            "cuisine": "american",
        }
        result = score_enriched_pairing("pinot noir", enriched)
        assert result is not None
        assert result["confidence"] == "high"

    def test_confidence_low_with_one_signal(self):
        enriched = {"protein": "chicken"}
        result = score_enriched_pairing("pinot noir", enriched)
        assert result is not None
        assert result["confidence"] == "low"

    def test_confidence_medium_with_two_signals(self):
        enriched = {"protein": "fish", "richness": "light"}
        result = score_enriched_pairing("sauvignon blanc", enriched)
        assert result is not None
        assert result["confidence"] == "medium"

    def test_partial_match(self):
        # Pinot noir matches chicken but not spice
        enriched = {"protein": "chicken", "spice_heat": "high"}
        result = score_enriched_pairing("pinot noir", enriched)
        assert result is not None
        assert result["score"] == "partial"

    def test_spice_heat_favors_riesling(self):
        # Riesling matches spice_heat=high but not protein=chicken → partial
        enriched = {"protein": "chicken", "spice_heat": "high"}
        result = score_enriched_pairing("riesling", enriched)
        assert result is not None
        assert result["score"] == "partial"

    def test_spice_only_riesling_good(self):
        # With only spice signal, riesling is a good match
        enriched = {"spice_heat": "high"}
        result = score_enriched_pairing("riesling", enriched)
        assert result is not None
        assert result["score"] == "good"

    def test_acidity_matching(self):
        enriched = {"acidity": "high"}
        result = score_enriched_pairing("sangiovese chianti", enriched)
        assert result is not None
        assert result["score"] == "good"

    def test_cuisine_matching_italian(self):
        enriched = {"cuisine": "italian", "richness": "medium"}
        result = score_enriched_pairing("sangiovese chianti classico", enriched)
        assert result is not None
        assert result["score"] == "good"

    def test_suggested_styles_on_mismatch(self):
        enriched = {"protein": "beef", "richness": "rich"}
        result = score_enriched_pairing("riesling", enriched)
        assert result is not None
        assert "suggested_styles" in result
        assert len(result["suggested_styles"]) > 0

    def test_list_valued_protein_does_not_crash(self):
        # The LLM occasionally returns a list for a scalar field when the dish
        # has multiple proteins. Must not raise TypeError on dict membership.
        enriched = {"protein": ["salmon", "shrimp"]}
        result = score_enriched_pairing("pinot noir", enriched)
        assert result is not None
        assert result["score"] == "good"

    def test_list_valued_protein_first_known_wins(self):
        # Unknown leading entries are skipped; first known key drives scoring.
        enriched = {"protein": ["unknownfish", "beef"]}
        result = score_enriched_pairing("cabernet sauvignon", enriched)
        assert result is not None
        assert result["score"] == "good"

    def test_list_valued_cuisine_does_not_crash(self):
        enriched = {"cuisine": ["italian", "american"], "richness": "medium"}
        result = score_enriched_pairing("sangiovese chianti", enriched)
        assert result is not None

    def test_list_with_no_known_keys_skipped(self):
        # A list of unrecognized values should produce no signal, not crash.
        enriched = {"protein": ["unobtanium"]}
        result = score_enriched_pairing("any wine", enriched)
        assert result is None


class TestBuildEnrichedIndex:
    def test_builds_index_from_enriched_list(self):
        enriched = [
            {
                "date": "2026-04-20",
                "meal": "Grilled chicken",
                "enriched": {"protein": "chicken"},
            }
        ]
        index = _build_enriched_index(enriched)
        assert "2026-04-20|Grilled chicken" in index

    def test_skips_entries_without_enriched(self):
        enriched = [{"date": "2026-04-20", "meal": "Leftovers", "enriched": None}]
        index = _build_enriched_index(enriched)
        assert len(index) == 0

    def test_empty_list(self):
        assert _build_enriched_index([]) == {}


class TestNotEnrichedMeal:
    """A meal with no enrichment falls back to the keyword result."""

    MENU = [{"date": "2026-04-20", "meal": "Grilled steak", "keywords": ["steak"]}]
    PLAN = [
        {
            "week": "Week 1",
            "date": "Apr 20, 2026",
            "vintage": 2019,
            "name": "Sauvignon Blanc",
            "appellation": "Marlborough",
            "badge": "white",
        }
    ]

    def _suggest(self, enriched_value):
        enriched = [{**self.MENU[0], "enriched": enriched_value}]
        return suggest_pairings(self.MENU, self.PLAN, [], enriched)

    def test_marked_meal_from_enrich_menu_gives_keyword_result(self, tmp_path):
        menu_path = tmp_path / "menu.json"
        cache_path = tmp_path / "cache.json"
        menu_path.write_text(json.dumps(self.MENU), encoding="utf-8")
        cache_path.write_text(
            json.dumps({text_hash("Grilled steak"): {"not_enriched_on": "2026-04-20"}}),
            encoding="utf-8",
        )
        with patch("enrich_menu.call_claude") as mock_claude:
            results = enrich_menu(
                menu_path, cache_path, today=date(2026, 4, 21)
            ).results
        mock_claude.assert_not_called()
        assert results[0]["enriched"] is None

        marked = suggest_pairings(self.MENU, self.PLAN, [], results)
        assert marked == suggest_pairings(self.MENU, self.PLAN, [], None)
        assert marked[0]["pairing"]["confidence"] == "medium"

    def test_empty_enriched_gives_same_result_as_none(self):
        empty = self._suggest({})
        assert empty == self._suggest(None)
        assert empty[0]["pairing"]["confidence"] == "medium"
