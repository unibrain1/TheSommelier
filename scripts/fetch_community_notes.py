#!/usr/bin/env python3
"""Fetch community tasting notes from CellarTracker RSS feed.

Polls rssnote.asp using a personal CK token, parses each <item> into
structured note records, and appends to a cumulative JSON cache keyed
by iWine. Never overwrites — only adds new notes (deduped by iNote).

Usage: fetch_community_notes.py [community_notes.json]
Env:   CT_COMMUNITY_NOTES_RSS must be set to the RSS feed URL.
       The default cache is $DATA_DIR/community_notes.json.

Exit codes:
  0  the feed was read and every item was merged, or the feed has no items
  1  a fetch error, a body that is not an RSS feed, every item was skipped, a corrupt
     cache or a cache that cannot be read, a failed write, no
     CT_COMMUNITY_NOTES_RSS, or a crash
  3  some, but not all, items were skipped (no iWine or no iNote). The cache
     was written.
"""

import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import settings


def sanitize_bbcode(text: str) -> str:
    """Strip BBCode markup (e.g., [url=...]...[/url])."""
    text = re.sub(r"\[url=[^\]]*\](.*?)\[/url\]", r"\1", text)
    text = re.sub(r"\[/?[a-zA-Z]+[^\]]*\]", "", text)
    return text.strip()


def parse_rss_item(item: ET.Element) -> dict | None:
    """Parse a single RSS <item> into a note record."""
    link = item.findtext("link", "")
    guid = item.findtext("guid", "")
    title = item.findtext("title", "").strip()
    pub_date = item.findtext("pubDate", "").strip()
    description = item.findtext("description", "").strip()

    # Extract iWine from link: .../notes.asp?iWine=12345...
    iwine_match = re.search(r"iWine=(\d+)", link or guid)
    if not iwine_match:
        return None
    iwine = iwine_match.group(1)

    # Extract iNote from link or guid: ...#iNote12345 or iNote=12345
    inote_match = re.search(r"iNote[=]?(\d+)", link or guid)
    inote = inote_match.group(1) if inote_match else ""

    if not inote:
        return None

    # Parse description for author, score, body, tasting_date
    author = ""
    author_match = re.match(r"^Tasted by (.+?)\.", description)
    if author_match:
        author = author_match.group(1).strip()

    score = None
    score_match = re.search(r"\((\d+)\s*pts?\.\)", description)
    if score_match:
        score = int(score_match.group(1))

    tasting_date = ""
    date_match = re.search(r"Tasted\s+(\d+/\d+/\d+)", description)
    if date_match:
        tasting_date = date_match.group(1)

    # Extract body: everything after the metadata line
    # Typical format: "Tasted by author. (92 pts.) Tasted 4/11/2026\n\nActual note body"
    body = description
    # Remove the first line (metadata)
    lines = description.split("\n", 1)
    if len(lines) > 1:
        body = lines[1].strip()
    else:
        # Try removing the metadata prefix
        body = re.sub(
            r"^Tasted by .+?\.\s*(\(\d+\s*pts?\.\))?\s*(Tasted\s+\d+/\d+/\d+)?\s*",
            "",
            body,
        ).strip()

    body = sanitize_bbcode(body)

    return {
        "iWine": iwine,
        "iNote": inote,
        "title": title,
        "pubDate": pub_date,
        "author": author,
        "score": score,
        "body": body if body else None,
        "tasting_date": tasting_date if tasting_date else None,
    }


class NotRssError(ValueError):
    """The body is not an RSS feed. The message holds no feed text."""


def parse_rss(xml_text: str) -> tuple[list[dict], int]:
    """Parse RSS XML into note records.

    Returns (notes, skipped). An item with no iWine or no iNote counts as
    skipped. Raises NotRssError when the text is not XML or the root element
    is not <rss>.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        # The parse error gives only a line and a column, no feed text.
        raise NotRssError(f"not XML: {e}") from e
    # A login page in XHTML is valid XML with no <item>. Without this check
    # it reads as an empty feed and gives exit 0.
    if root.tag != "rss":
        raise NotRssError("the root element is not rss")

    notes = []
    skipped = 0
    for item in root.findall(".//item"):
        record = parse_rss_item(item)
        if record:
            notes.append(record)
        else:
            skipped += 1
    return notes, skipped


class CorruptCacheError(Exception):
    """The cache file exists but does not hold a JSON object."""


class CacheReadError(CorruptCacheError):
    """The cache file cannot be read, for example because of a permission error.

    The file can hold good history, so the fetch must not tell the operator to
    delete it.
    """


def read_cache(cache_path: Path) -> dict[str, list[dict]]:
    """Read the cumulative cache (keyed by iWine).

    A missing or empty file gives {}. Raises CacheReadError when the file
    cannot be read. Raises CorruptCacheError when the file is not JSON or is
    not a JSON object.
    """
    try:
        if not cache_path.exists() or cache_path.stat().st_size == 0:
            return {}
        text = cache_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise CorruptCacheError(f"{type(e).__name__}: {e}") from e
    except OSError as e:
        raise CacheReadError(f"{type(e).__name__}: {e}") from e
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise CorruptCacheError(f"{type(e).__name__}: {e}") from e
    if not isinstance(data, dict):
        raise CorruptCacheError(f"JSON {type(data).__name__}, not an object")
    return data


def load_cache(cache_path: Path) -> dict[str, list[dict]]:
    """Read the cache, or give {} with a warning when it is corrupt.

    Readers that do not write the cache use this. The fetch uses read_cache,
    so that it does not write over a corrupt cache.
    """
    try:
        return read_cache(cache_path)
    except CorruptCacheError as e:
        print(f"WARNING: Could not load cache: {e}", file=sys.stderr)
        return {}


def merge_notes(
    cache: dict[str, list[dict]], new_notes: list[dict]
) -> tuple[dict[str, list[dict]], int]:
    """Merge new notes into cache, deduplicating by iNote. Returns (cache, added_count)."""
    # Build a set of existing iNote IDs for fast lookup
    existing_inotes: set[str] = set()
    for notes_list in cache.values():
        for note in notes_list:
            existing_inotes.add(note["iNote"])

    added = 0
    for note in new_notes:
        if note["iNote"] in existing_inotes:
            continue
        iwine = note["iWine"]
        cache.setdefault(iwine, []).append(note)
        existing_inotes.add(note["iNote"])
        added += 1

    # Sort each wine's notes by tasting_date descending (most recent first)
    def _parse_date(n: dict) -> datetime:
        td = n.get("tasting_date")
        if td:
            try:
                return datetime.strptime(td, "%m/%d/%Y")
            except ValueError:
                pass
        return datetime.min

    for iwine in cache:
        cache[iwine].sort(key=_parse_date, reverse=True)

    return cache, added


def fetch_community_notes(rss_url: str, cache_path: Path) -> int:
    """Fetch the RSS feed and merge it into the cumulative cache.

    Returns the exit code (see the module docstring).
    """
    try:
        req = urllib.request.Request(
            rss_url, headers={"User-Agent": "The-Sommelier/1.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            xml_text = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        # Some error texts repeat the URL (for example InvalidURL and
        # ValueError), and the URL holds the CK token. The HTTP status is safe, and it can tell a token
        # problem from an outage.
        status = f" {e.code}" if isinstance(e, urllib.error.HTTPError) else ""
        print(f"ERROR: RSS fetch failed ({type(e).__name__}{status})", file=sys.stderr)
        return 1

    try:
        new_notes, skipped = parse_rss(xml_text)
    except NotRssError as e:
        # The body can be an HTML login page. The log shows the error, which
        # holds no feed text. The log does not show the body or the URL,
        # because the URL holds the CK token.
        print(
            f"ERROR: RSS body is not an RSS feed ({e}) — check CT_COMMUNITY_NOTES_RSS",
            file=sys.stderr,
        )
        return 1

    # Read the cache before any write. A corrupt cache merged as {} would
    # replace the cumulative history with only this feed.
    try:
        cache = read_cache(cache_path)
    except CacheReadError as e:
        print(
            f"ERROR: community notes cache {cache_path} could not be read ({e}) — "
            "not overwritten. Check the file permissions and the data mount.",
            file=sys.stderr,
        )
        return 1
    except CorruptCacheError as e:
        print(
            f"ERROR: community notes cache {cache_path} is corrupt ({e}) — "
            "not overwritten. Repair or delete the file.",
            file=sys.stderr,
        )
        return 1

    if not new_notes:
        if skipped:
            print(
                f"ERROR: all {skipped} RSS items skipped (no iWine or no iNote)",
                file=sys.stderr,
            )
            return 1
        print("No notes found in RSS feed.")
        return 0

    cache, added = merge_notes(cache, new_notes)

    # Write atomically (tmp + rename) to protect cumulative cache from partial writes
    tmp_path = cache_path.with_suffix(".tmp")
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(
            json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        tmp_path.replace(cache_path)
    except OSError as e:
        print(f"ERROR: Failed to write community notes cache: {e}", file=sys.stderr)
        # The write error is the one to report. A failed cleanup must not
        # replace it with a traceback.
        try:
            if tmp_path.is_file():
                tmp_path.unlink()
        except OSError:
            pass
        return 1

    total = sum(len(v) for v in cache.values())
    wines = len(cache)
    print(f"Community notes: {added} new, {total} total across {wines} wines")
    if skipped:
        print(
            f"WARNING: {skipped} RSS items skipped (no iWine or no iNote)",
            file=sys.stderr,
        )
        return 3
    return 0


def main(argv: list[str]) -> int:
    """Run the script and return its exit code (see the module docstring)."""
    url = settings.community_notes_url()
    if not url:
        print(
            "ERROR: CT_COMMUNITY_NOTES_RSS environment variable not set",
            file=sys.stderr,
        )
        return 1

    cache_path = (
        Path(argv[1]) if len(argv) > 1 else settings.data_dir() / "community_notes.json"
    )
    return fetch_community_notes(url, cache_path)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
