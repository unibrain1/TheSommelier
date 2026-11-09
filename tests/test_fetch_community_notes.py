"""Tests for fetch_community_notes.py — RSS parsing, dedup, cumulative cache."""

import json

import pytest

from fetch_community_notes import (
    load_cache,
    merge_notes,
    parse_rss,
    parse_rss_item,
    sanitize_bbcode,
)

# ---------------------------------------------------------------------------
# Sample RSS XML
# ---------------------------------------------------------------------------

SAMPLE_RSS = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>CellarTracker Community Notes</title>
<item>
  <title>2024 Drouhin Oregon Roserock Pinot Noir</title>
  <link>https://www.cellartracker.com/notes.asp?iWine=5522345#iNote12483504</link>
  <guid>https://www.cellartracker.com/notes.asp?iWine=5522345#iNote12483504</guid>
  <pubDate>Thu, 16 Apr 2026 00:00:00 PST</pubDate>
  <description>Tasted by nonodannecy. (92 pts.) Tasted 4/11/2026
Beautiful bright ruby. Cherry and raspberry with earthy undertones.</description>
</item>
<item>
  <title>2018 Domaine Drouhin Oregon Pinot Noir Laurène</title>
  <link>https://www.cellartracker.com/notes.asp?iWine=3344556#iNote12483505</link>
  <guid>https://www.cellartracker.com/notes.asp?iWine=3344556#iNote12483505</guid>
  <pubDate>Wed, 15 Apr 2026 00:00:00 PST</pubDate>
  <description>Tasted by winecritic42. Tasted 4/10/2026
Silky and refined, showing great depth.</description>
</item>
<item>
  <title>2020 Adamant Cellars Syrah Artisan</title>
  <link>https://www.cellartracker.com/notes.asp?iWine=7788990#iNote12483506</link>
  <guid>https://www.cellartracker.com/notes.asp?iWine=7788990#iNote12483506</guid>
  <pubDate>Tue, 14 Apr 2026 00:00:00 PST</pubDate>
  <description>Tasted by reviewer123. (88 pts.) Tasted 4/9/2026
Dark fruit, pepper, past prime now. Drink it now before it fades.</description>
</item>
</channel>
</rss>"""


# ---------------------------------------------------------------------------
# TestSanitizeBBCode
# ---------------------------------------------------------------------------


class TestSanitizeBBCode:
    def test_strips_url_bbcode(self):
        assert (
            sanitize_bbcode("[url=http://example.com]click here[/url]") == "click here"
        )

    def test_strips_nested_bbcode(self):
        assert sanitize_bbcode("[b]bold[/b] text") == "bold text"

    def test_no_bbcode_unchanged(self):
        assert sanitize_bbcode("just plain text") == "just plain text"

    def test_empty_string(self):
        assert sanitize_bbcode("") == ""

    def test_multiple_url_tags(self):
        text = "[url=a]one[/url] and [url=b]two[/url]"
        assert sanitize_bbcode(text) == "one and two"


# ---------------------------------------------------------------------------
# TestParseRssItem
# ---------------------------------------------------------------------------


class TestParseRssItem:
    def _make_item(self, link, description, title="Wine"):
        import xml.etree.ElementTree as ET

        item = ET.Element("item")
        ET.SubElement(item, "title").text = title
        ET.SubElement(item, "link").text = link
        ET.SubElement(item, "guid").text = link
        ET.SubElement(item, "pubDate").text = "Thu, 16 Apr 2026 00:00:00 PST"
        ET.SubElement(item, "description").text = description
        return item

    def test_extracts_iwine_and_inote(self):
        item = self._make_item(
            "https://ct.com/notes.asp?iWine=123#iNote456",
            "Tasted by alice. (90 pts.) Tasted 4/1/2026\nGreat wine.",
        )
        result = parse_rss_item(item)
        assert result is not None
        assert result["iWine"] == "123"
        assert result["iNote"] == "456"

    def test_extracts_author(self):
        item = self._make_item(
            "https://ct.com/notes.asp?iWine=1#iNote2",
            "Tasted by john_doe. (88 pts.) Tasted 1/1/2026\nNice.",
        )
        result = parse_rss_item(item)
        assert result["author"] == "john_doe"

    def test_extracts_score(self):
        item = self._make_item(
            "https://ct.com/notes.asp?iWine=1#iNote2",
            "Tasted by x. (95 pts.) Tasted 1/1/2026\nGreat.",
        )
        result = parse_rss_item(item)
        assert result["score"] == 95

    def test_missing_score_is_none(self):
        item = self._make_item(
            "https://ct.com/notes.asp?iWine=1#iNote2",
            "Tasted by x. Tasted 1/1/2026\nDecent wine.",
        )
        result = parse_rss_item(item)
        assert result["score"] is None

    def test_extracts_tasting_date(self):
        item = self._make_item(
            "https://ct.com/notes.asp?iWine=1#iNote2",
            "Tasted by x. Tasted 4/11/2026\nGood.",
        )
        result = parse_rss_item(item)
        assert result["tasting_date"] == "4/11/2026"

    def test_missing_inote_returns_none(self):
        item = self._make_item(
            "https://ct.com/notes.asp?iWine=123",
            "Tasted by x. Tasted 1/1/2026\nNote.",
        )
        result = parse_rss_item(item)
        assert result is None

    def test_missing_iwine_returns_none(self):
        item = self._make_item(
            "https://ct.com/notes.asp?something=else#iNote456",
            "Tasted by x.\nNote.",
        )
        result = parse_rss_item(item)
        assert result is None


# ---------------------------------------------------------------------------
# TestParseRss
# ---------------------------------------------------------------------------


class TestParseRss:
    def test_parses_sample_rss(self):
        notes = parse_rss(SAMPLE_RSS)
        assert len(notes) == 3

    def test_first_note_fields(self):
        notes = parse_rss(SAMPLE_RSS)
        n = notes[0]
        assert n["iWine"] == "5522345"
        assert n["iNote"] == "12483504"
        assert n["author"] == "nonodannecy"
        assert n["score"] == 92
        assert n["tasting_date"] == "4/11/2026"
        assert "Cherry and raspberry" in (n["body"] or "")

    def test_note_without_score(self):
        notes = parse_rss(SAMPLE_RSS)
        n = notes[1]  # winecritic42 has no score
        assert n["score"] is None
        assert n["author"] == "winecritic42"

    def test_invalid_xml_returns_empty(self):
        assert parse_rss("not xml at all") == []

    def test_empty_channel_returns_empty(self):
        xml = '<?xml version="1.0"?><rss><channel></channel></rss>'
        assert parse_rss(xml) == []


# ---------------------------------------------------------------------------
# TestMergeNotes
# ---------------------------------------------------------------------------


class TestMergeNotes:
    def _note(self, iwine, inote, score=None, tasting_date=None):
        return {
            "iWine": iwine,
            "iNote": inote,
            "title": "Wine",
            "pubDate": "",
            "author": "tester",
            "score": score,
            "body": "test note",
            "tasting_date": tasting_date,
        }

    def test_merge_into_empty_cache(self):
        cache, added = merge_notes({}, [self._note("1", "100")])
        assert added == 1
        assert "1" in cache
        assert len(cache["1"]) == 1

    def test_dedup_by_inote(self):
        existing = {"1": [self._note("1", "100")]}
        cache, added = merge_notes(existing, [self._note("1", "100")])
        assert added == 0
        assert len(cache["1"]) == 1

    def test_adds_new_note_to_existing_wine(self):
        existing = {"1": [self._note("1", "100")]}
        cache, added = merge_notes(existing, [self._note("1", "101")])
        assert added == 1
        assert len(cache["1"]) == 2

    def test_adds_note_for_new_wine(self):
        existing = {"1": [self._note("1", "100")]}
        cache, added = merge_notes(existing, [self._note("2", "200")])
        assert added == 1
        assert "2" in cache

    def test_sorts_by_tasting_date_descending(self):
        notes = [
            self._note("1", "100", tasting_date="1/1/2026"),
            self._note("1", "101", tasting_date="4/1/2026"),
            self._note("1", "102", tasting_date="2/1/2026"),
        ]
        cache, _ = merge_notes({}, notes)
        dates = [n["tasting_date"] for n in cache["1"]]
        assert dates == ["4/1/2026", "2/1/2026", "1/1/2026"]

    def test_multiple_new_notes_counted(self):
        notes = [self._note("1", "100"), self._note("1", "101"), self._note("2", "200")]
        cache, added = merge_notes({}, notes)
        assert added == 3


# ---------------------------------------------------------------------------
# TestLoadCache
# ---------------------------------------------------------------------------


class TestLoadCache:
    def test_missing_file_returns_empty(self, tmp_path):
        assert load_cache(tmp_path / "nonexistent.json") == {}

    def test_empty_file_returns_empty(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text("")
        assert load_cache(p) == {}

    def test_valid_cache(self, tmp_path):
        p = tmp_path / "cache.json"
        data = {"123": [{"iNote": "1", "body": "test"}]}
        p.write_text(json.dumps(data))
        assert load_cache(p) == data

    def test_invalid_json_returns_empty(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{invalid json")
        assert load_cache(p) == {}


# ---------------------------------------------------------------------------
# Fetch error text
# ---------------------------------------------------------------------------


def test_fetch_error_prints_type_only_never_url_or_token(tmp_path, monkeypatch, capsys):
    import fetch_community_notes as fcn

    url = "https://www.cellartracker.com/rssnote.asp?CK=SECRET-canary-ck-token"

    def explode(request, timeout=None):
        raise ValueError(f"bad request for {request.full_url} {url}")

    monkeypatch.setattr(fcn.urllib.request, "urlopen", explode)
    cache = tmp_path / "community_notes.json"

    with pytest.raises(SystemExit) as exc:
        fcn.fetch_community_notes(url, cache)

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == "ERROR: RSS fetch failed (ValueError)"
    assert "SECRET-canary" not in captured.out + captured.err
    assert "cellartracker.com" not in captured.out + captured.err
    assert not cache.exists()
