"""Tests for fetch_community_notes.py — RSS parsing, dedup, cumulative cache."""

import io
import json
import xml.etree.ElementTree as ET

import pytest

import fetch_community_notes as fcn
from fetch_community_notes import (
    CorruptCacheError,
    load_cache,
    merge_notes,
    parse_rss,
    parse_rss_item,
    read_cache,
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
        notes, skipped = parse_rss(SAMPLE_RSS)
        assert len(notes) == 3
        assert skipped == 0

    def test_first_note_fields(self):
        notes, _ = parse_rss(SAMPLE_RSS)
        n = notes[0]
        assert n["iWine"] == "5522345"
        assert n["iNote"] == "12483504"
        assert n["author"] == "nonodannecy"
        assert n["score"] == 92
        assert n["tasting_date"] == "4/11/2026"
        assert "Cherry and raspberry" in (n["body"] or "")

    def test_note_without_score(self):
        notes, _ = parse_rss(SAMPLE_RSS)
        n = notes[1]  # winecritic42 has no score
        assert n["score"] is None
        assert n["author"] == "winecritic42"

    def test_invalid_xml_raises(self):
        with pytest.raises(fcn.NotRssError, match="not XML"):
            parse_rss("not xml at all")

    def test_xml_that_is_not_rss_raises(self):
        with pytest.raises(fcn.NotRssError, match="root element is not rss"):
            parse_rss("<html><body>Please log in</body></html>")

    def test_empty_channel_returns_empty(self):
        xml = '<?xml version="1.0"?><rss><channel></channel></rss>'
        assert parse_rss(xml) == ([], 0)

    def test_counts_skipped_items(self):
        notes, skipped = parse_rss(PARTIAL_RSS)
        assert [n["iNote"] for n in notes] == ["12483504"]
        assert skipped == 1


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

    def test_invalid_json_returns_empty(self, tmp_path, capsys):
        p = tmp_path / "bad.json"
        p.write_text("{invalid json")
        assert load_cache(p) == {}
        assert "WARNING: Could not load cache" in capsys.readouterr().err

    def test_non_object_returns_empty(self, tmp_path, capsys):
        p = tmp_path / "list.json"
        p.write_text("[1, 2]")
        assert load_cache(p) == {}
        assert "WARNING: Could not load cache" in capsys.readouterr().err


class TestReadCache:
    def test_missing_file_returns_empty(self, tmp_path):
        assert read_cache(tmp_path / "nonexistent.json") == {}

    def test_invalid_json_raises(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{invalid json")
        with pytest.raises(CorruptCacheError):
            read_cache(p)

    def test_non_object_raises(self, tmp_path):
        p = tmp_path / "list.json"
        p.write_text("[1, 2]")
        with pytest.raises(CorruptCacheError):
            read_cache(p)

    def test_read_error_raises_cache_read_error(self, tmp_path):
        p = tmp_path / "folder.json"
        p.mkdir()
        with pytest.raises(fcn.CacheReadError):
            read_cache(p)

    def test_bytes_that_are_not_utf8_raise_corrupt_not_read_error(self, tmp_path):
        p = tmp_path / "latin1.json"
        p.write_bytes(b'{"a": "\xff"}')
        with pytest.raises(CorruptCacheError) as info:
            read_cache(p)
        assert not isinstance(info.value, fcn.CacheReadError)


# ---------------------------------------------------------------------------
# Fetch error text
# ---------------------------------------------------------------------------


def test_fetch_error_prints_type_only_never_url_or_token(tmp_path, monkeypatch, capsys):
    url = "https://www.cellartracker.com/rssnote.asp?CK=SECRET-canary-ck-token"

    def explode(request, timeout=None):
        raise ValueError(f"bad request for {request.full_url} {url}")

    monkeypatch.setattr(fcn.urllib.request, "urlopen", explode)
    cache = tmp_path / "community_notes.json"

    assert fcn.fetch_community_notes(url, cache) == 1

    captured = capsys.readouterr()
    assert captured.err.strip() == "ERROR: RSS fetch failed (ValueError)"
    assert "SECRET-canary" not in captured.out + captured.err
    assert "cellartracker.com" not in captured.out + captured.err
    assert not cache.exists()


# ---------------------------------------------------------------------------
# Exit codes of fetch_community_notes() and main()
# ---------------------------------------------------------------------------

FEED_URL = "https://www.cellartracker.com/rssnote.asp?CK=SECRET-canary-ck-token"

EMPTY_RSS = '<?xml version="1.0"?><rss><channel><title>x</title></channel></rss>'

NO_INOTE_ITEM = """<item>
  <title>No note id</title>
  <link>https://www.cellartracker.com/notes.asp?iWine=999</link>
  <guid>https://www.cellartracker.com/notes.asp?iWine=999</guid>
  <description>Tasted by nobody.
No note id here.</description>
</item>"""

PARTIAL_RSS = f"""<?xml version="1.0"?>
<rss><channel>
<item>
  <title>2024 Drouhin Oregon Roserock Pinot Noir</title>
  <link>https://www.cellartracker.com/notes.asp?iWine=5522345#iNote12483504</link>
  <guid>https://www.cellartracker.com/notes.asp?iWine=5522345#iNote12483504</guid>
  <description>Tasted by nonodannecy. (92 pts.) Tasted 4/11/2026
Cherry.</description>
</item>
{NO_INOTE_ITEM}
</channel></rss>"""

ALL_SKIPPED_RSS = f"""<?xml version="1.0"?>
<rss><channel>
{NO_INOTE_ITEM}
</channel></rss>"""

HTML_BODY = """<!DOCTYPE html>
<html><head><title>CellarTracker Login</title></head>
<body><p>Please sign in secret-login-page-body &nbsp; to continue</p>
<br></body></html>"""


XHTML_BODY = "<!DOCTYPE html><html><body><p>secret-login-page-body</p></body></html>"


def _serve(monkeypatch, body: str) -> None:
    def fake_urlopen(request, timeout=None):
        return io.BytesIO(body.encode("utf-8"))

    monkeypatch.setattr(fcn.urllib.request, "urlopen", fake_urlopen)


class TestFetchExitCodes:
    def test_valid_feed_returns_0_and_writes_cache(self, tmp_path, monkeypatch):
        _serve(monkeypatch, SAMPLE_RSS)
        cache = tmp_path / "community_notes.json"

        assert fcn.fetch_community_notes(FEED_URL, cache) == 0

        data = json.loads(cache.read_text(encoding="utf-8"))
        assert sorted(data) == ["3344556", "5522345", "7788990"]

    def test_feed_with_no_items_returns_0(self, tmp_path, monkeypatch):
        _serve(monkeypatch, EMPTY_RSS)
        cache = tmp_path / "community_notes.json"

        assert fcn.fetch_community_notes(FEED_URL, cache) == 0

    def test_some_items_skipped_returns_3(self, tmp_path, monkeypatch, capsys):
        _serve(monkeypatch, PARTIAL_RSS)
        cache = tmp_path / "community_notes.json"

        assert fcn.fetch_community_notes(FEED_URL, cache) == 3

        data = json.loads(cache.read_text(encoding="utf-8"))
        assert list(data) == ["5522345"]
        assert data["5522345"][0]["iNote"] == "12483504"
        err = capsys.readouterr().err
        assert "WARNING: 1 RSS items skipped" in err

    def test_html_body_returns_1_without_body_or_url(
        self, tmp_path, monkeypatch, capsys
    ):
        _serve(monkeypatch, HTML_BODY)
        cache = tmp_path / "community_notes.json"

        assert fcn.fetch_community_notes(FEED_URL, cache) == 1

        captured = capsys.readouterr()
        assert captured.err.startswith("ERROR: RSS body is not an RSS feed (not XML:")
        assert "check CT_COMMUNITY_NOTES_RSS" in captured.err
        output = captured.out + captured.err
        assert "secret-login-page-body" not in output
        assert "SECRET-canary" not in output
        assert "cellartracker.com" not in output
        assert not cache.exists()

    def test_well_formed_html_body_returns_1(self, tmp_path, monkeypatch, capsys):
        # An XHTML login page parses as XML but has no <rss> root.
        _serve(monkeypatch, XHTML_BODY)
        cache = tmp_path / "community_notes.json"

        assert fcn.fetch_community_notes(FEED_URL, cache) == 1

        captured = capsys.readouterr()
        assert captured.err.startswith(
            "ERROR: RSS body is not an RSS feed (the root element is not rss)"
        )
        assert "secret-login-page-body" not in captured.out + captured.err
        assert not cache.exists()

    def test_all_items_skipped_returns_1_without_write(
        self, tmp_path, monkeypatch, capsys
    ):
        _serve(monkeypatch, ALL_SKIPPED_RSS)
        cache = tmp_path / "community_notes.json"

        assert fcn.fetch_community_notes(FEED_URL, cache) == 1

        assert not cache.exists()
        assert "ERROR: all 1 RSS items skipped" in capsys.readouterr().err

    @pytest.mark.parametrize("content", ["{invalid json", "[1, 2]"])
    def test_corrupt_cache_returns_1_and_keeps_file(
        self, tmp_path, monkeypatch, capsys, content
    ):
        _serve(monkeypatch, SAMPLE_RSS)
        cache = tmp_path / "community_notes.json"
        cache.write_text(content, encoding="utf-8")
        before = cache.read_bytes()

        assert fcn.fetch_community_notes(FEED_URL, cache) == 1

        assert cache.read_bytes() == before
        assert not cache.with_suffix(".tmp").exists()
        err = capsys.readouterr().err
        assert f"ERROR: community notes cache {cache} is corrupt" in err
        assert "not overwritten" in err

    def test_unreadable_cache_returns_1_without_delete_advice(
        self, tmp_path, monkeypatch, capsys
    ):
        _serve(monkeypatch, SAMPLE_RSS)
        cache = tmp_path / "community_notes.json"
        # A folder at the cache path gives an OSError on read, not bad JSON.
        cache.mkdir()

        assert fcn.fetch_community_notes(FEED_URL, cache) == 1

        assert cache.is_dir()
        err = capsys.readouterr().err
        assert f"ERROR: community notes cache {cache} could not be read" in err
        assert "not overwritten" in err
        assert "corrupt" not in err
        assert "delete" not in err

    def test_write_failure_returns_1(self, tmp_path, monkeypatch, capsys):
        _serve(monkeypatch, SAMPLE_RSS)
        cache = tmp_path / "community_notes.json"
        # A folder at the tmp path makes the write fail.
        cache.with_suffix(".tmp").mkdir()

        assert fcn.fetch_community_notes(FEED_URL, cache) == 1

        assert not cache.exists()
        assert "ERROR: Failed to write community notes cache" in (
            capsys.readouterr().err
        )


class TestMain:
    def test_without_url_returns_1(self, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv("CT_COMMUNITY_NOTES_RSS", raising=False)
        cache = tmp_path / "community_notes.json"

        assert fcn.main(["fetch_community_notes.py", str(cache)]) == 1

        assert "CT_COMMUNITY_NOTES_RSS" in capsys.readouterr().err
        assert not cache.exists()

    def test_returns_exit_code_of_fetch(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CT_COMMUNITY_NOTES_RSS", FEED_URL)
        _serve(monkeypatch, PARTIAL_RSS)
        cache = tmp_path / "community_notes.json"

        assert fcn.main(["fetch_community_notes.py", str(cache)]) == 3
