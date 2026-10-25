"""Tests for generate_digest.py — digest generation logic."""

import base64
import json
from pathlib import Path

import pytest

import generate_digest
from generate_digest import (
    _logo_src,
    build_digest,
    find_meals_for_date,
    format_digest_html,
)
from wine_utils import find_current_week


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _week(date_str: str, name: str = "Test Wine", vintage: str = "2020") -> dict:
    return {
        "week": 1,
        "date": date_str,
        "name": name,
        "vintage": vintage,
        "badge": "red",
        "note": "A fine wine.",
        "window": "2020–2025",
        "score": "CT90",
        "location": "Cellar",
        "urgent": False,
    }


def _suggestion(date_str: str, meal: str, score: str = "good") -> dict:
    return {
        "date": date_str,
        "meal": meal,
        "pairing": {"score": score, "details": "test"},
    }


def _digest() -> dict:
    return {
        "date": "2026-04-18",
        "date_display": "Friday, April 18, 2026",
        "has_content": False,
        "wine": None,
        "pairings": [],
    }


# ---------------------------------------------------------------------------
# TestFindCurrentWeek
# ---------------------------------------------------------------------------


class TestFindCurrentWeek:
    def test_finds_matching_week(self):
        from datetime import date, timedelta

        today = date.today()
        monday = today - timedelta(days=today.weekday())
        week = _week(monday.strftime("%b %-d, %Y"))
        result = find_current_week([week], today)
        assert result is not None
        assert result["name"] == "Test Wine"

    def test_returns_none_when_no_match(self):
        from datetime import date

        result = find_current_week([_week("Jan 1, 2020")], date(2026, 6, 15))
        assert result is None

    def test_empty_list(self):
        from datetime import date

        assert find_current_week([], date.today()) is None


# ---------------------------------------------------------------------------
# TestFindMealsForDate
# ---------------------------------------------------------------------------


class TestFindMealsForDate:
    def test_finds_meals(self):
        suggestions = [
            _suggestion("2026-04-18", "Pasta"),
            _suggestion("2026-04-19", "Steak"),
            _suggestion("2026-04-18", "Salad"),
        ]
        result = find_meals_for_date(suggestions, "2026-04-18")
        assert len(result) == 2

    def test_no_meals(self):
        result = find_meals_for_date([_suggestion("2026-04-18", "Pasta")], "2026-04-20")
        assert len(result) == 0

    def test_empty_list(self):
        assert find_meals_for_date([], "2026-04-18") == []


# ---------------------------------------------------------------------------
# TestBuildDigest
# ---------------------------------------------------------------------------


class TestBuildDigest:
    def test_has_content_when_pairings_exist(self, tmp_path):
        from datetime import date, timedelta

        today = date.today()
        monday = today - timedelta(days=today.weekday())

        plan = {
            "allWeeks": [_week(monday.strftime("%b %-d, %Y"))],
        }
        pairings = {
            "suggestions": [_suggestion(today.isoformat(), "Chicken dinner")],
        }

        plan_path = tmp_path / "plan.json"
        pairing_path = tmp_path / "pairings.json"
        plan_path.write_text(json.dumps(plan))
        pairing_path.write_text(json.dumps(pairings))

        digest = build_digest(plan_path, pairing_path)
        assert digest["has_content"] is True
        assert len(digest["pairings"]) == 1
        assert digest["wine"] is not None

    def test_no_content_when_no_pairings(self, tmp_path):
        from datetime import date, timedelta

        today = date.today()
        monday = today - timedelta(days=today.weekday())

        plan = {"allWeeks": [_week(monday.strftime("%b %-d, %Y"))]}
        pairings = {"suggestions": []}

        plan_path = tmp_path / "plan.json"
        pairing_path = tmp_path / "pairings.json"
        plan_path.write_text(json.dumps(plan))
        pairing_path.write_text(json.dumps(pairings))

        digest = build_digest(plan_path, pairing_path)
        assert digest["has_content"] is False


# ---------------------------------------------------------------------------
# TestFormatDigestHtml
# ---------------------------------------------------------------------------


class TestFormatDigestHtml:
    def test_contains_wine_name(self):
        digest = {
            "date": "2026-04-18",
            "date_display": "Friday, April 18, 2026",
            "has_content": True,
            "wine": {
                "name": "Dion Vineyard Pinot Noir",
                "vintage": "2020",
                "badge": "red",
                "note": "Great wine.",
                "window": "2023–2028",
                "score": "CT90",
                "location": "Cellar",
                "urgent": False,
            },
            "pairings": [],
        }
        html = format_digest_html(digest)
        assert "Dion Vineyard Pinot Noir" in html
        assert "2020" in html

    def test_good_pairing_shows_no_swap_needed(self):
        digest = {
            "date": "2026-04-18",
            "date_display": "Friday, April 18, 2026",
            "has_content": True,
            "wine": None,
            "tonight": None,
            "pairings": [
                {
                    "meal": "Grilled chicken",
                    "pairing": {"score": "good", "details": "matches well"},
                }
            ],
        }
        html = format_digest_html(digest)
        assert "Grilled chicken" in html
        assert "no swap needed" in html

    def test_tonight_suggestion_is_primary(self):
        digest = {
            "date": "2026-04-18",
            "date_display": "Friday, April 18, 2026",
            "has_content": True,
            "wine": None,
            "tonight": {
                "meal": "Pasta",
                "bottle": {
                    "vintage": "2018",
                    "wine": "Laurène Pinot Noir",
                    "type": "Red",
                    "window": "2022–2031",
                    "urgency": "in peak window",
                },
                "pairing": {"score": "poor", "details": "no match"},
            },
            "pairings": [
                {
                    "meal": "Pasta",
                    "pairing": {"score": "poor", "details": "no match"},
                    "suggested_bottle": {
                        "vintage": "2018",
                        "wine": "Laurène Pinot Noir",
                        "window": "2022–2031",
                        "urgency": "in peak window",
                    },
                }
            ],
        }
        html = format_digest_html(digest)
        assert "Pull for Tonight" in html
        assert "Laurène Pinot Noir" in html

    def test_urgent_wine_shows_warning(self):
        digest = {
            "date": "2026-04-18",
            "date_display": "Friday, April 18, 2026",
            "has_content": True,
            "wine": {
                "name": "Old Wine",
                "vintage": "2010",
                "badge": "red",
                "note": "",
                "window": "2015–2020",
                "score": None,
                "location": "",
                "urgent": True,
            },
            "pairings": [],
        }
        html = format_digest_html(digest)
        assert "Past peak" in html

    def test_branding_present(self):
        digest = {
            "date": "2026-04-18",
            "date_display": "Friday, April 18, 2026",
            "has_content": False,
            "wine": None,
            "pairings": [],
        }
        html = format_digest_html(digest)
        assert "The Sommelier" in html
        assert "Drink the right bottles at the right time" in html


# ---------------------------------------------------------------------------
# TestLogoSrc
# ---------------------------------------------------------------------------


class TestLogoSrc:
    def test_logo_file_returns_data_uri(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SITE_URL", raising=False)
        (tmp_path / "logo.png").write_bytes(b"png-bytes")
        expected = "data:image/png;base64," + base64.b64encode(b"png-bytes").decode()
        assert _logo_src(tmp_path) == expected

    def test_logo_file_wins_over_site_url(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SITE_URL", "https://example.com")
        (tmp_path / "logo.png").write_bytes(b"png-bytes")
        assert _logo_src(tmp_path).startswith("data:image/png;base64,")

    def test_site_url_used_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SITE_URL", "https://example.com")
        assert _logo_src(tmp_path) == "https://example.com/logo.png"

    def test_http_site_url_is_accepted(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SITE_URL", "http://example.test")
        assert _logo_src(tmp_path) == "http://example.test/logo.png"

    def test_trailing_slashes_are_stripped(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SITE_URL", "https://example.com///")
        assert _logo_src(tmp_path) == "https://example.com/logo.png"

    def test_site_url_is_escaped(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SITE_URL", 'https://example.com/a"b&c=d')
        result = _logo_src(tmp_path)
        assert '"' not in result
        assert "&quot;" in result
        assert "&amp;" in result

    def test_unset_returns_empty_and_warns(self, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv("SITE_URL", raising=False)
        assert _logo_src(tmp_path) == ""
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.startswith("WARNING:")
        assert len(captured.err.strip().splitlines()) == 1

    def test_empty_site_url_returns_empty_and_warns(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("SITE_URL", "")
        assert _logo_src(tmp_path) == ""
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.startswith("WARNING:")

    @pytest.mark.parametrize("value", ["javascript:alert(1)", "sommelier.example.com"])
    def test_non_http_site_url_returns_empty_and_warns(
        self, tmp_path, monkeypatch, capsys, value
    ):
        monkeypatch.setenv("SITE_URL", value)
        assert _logo_src(tmp_path) == ""
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.startswith("WARNING:")

    def test_site_url_whitespace_stripped(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SITE_URL", "  https://example.test/\n")
        assert _logo_src(tmp_path) == "https://example.test/logo.png"

    def test_no_warning_when_file_found(self, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv("SITE_URL", raising=False)
        (tmp_path / "logo.png").write_bytes(b"png-bytes")
        _logo_src(tmp_path)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_no_warning_when_site_url_used(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("SITE_URL", "https://example.com")
        _logo_src(tmp_path)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""


# ---------------------------------------------------------------------------
# TestFormatDigestHtmlLogo
# ---------------------------------------------------------------------------


class TestFormatDigestHtmlLogo:
    def test_img_present_with_logo_src(self):
        html = format_digest_html(_digest(), logo_src="https://example.com/logo.png")
        assert '<img src="https://example.com/logo.png"' in html

    def test_img_omitted_when_logo_src_empty(self):
        assert "<img" not in format_digest_html(_digest(), logo_src="")

    def test_img_omitted_with_default_argument(self):
        assert "<img" not in format_digest_html(_digest())

    def test_output_has_no_unibrain(self):
        assert "unibrain" not in format_digest_html(_digest())


class TestSourceGuard:
    def test_module_source_has_no_unibrain(self):
        assert "unibrain" not in Path(generate_digest.__file__).read_text()
