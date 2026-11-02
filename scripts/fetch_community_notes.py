#!/usr/bin/env python3
"""Fetch community tasting notes from CellarTracker RSS feed.

Polls rssnote.asp using a personal CK token, parses each <item> into
structured note records, and appends to a cumulative JSON cache keyed
by iWine. Never overwrites — only adds new notes (deduped by iNote).

Usage: fetch_community_notes.py [community_notes.json]
Env:   CT_COMMUNITY_NOTES_RSS must be set to the RSS feed URL.
       The default cache is $DATA_DIR/community_notes.json.
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


def parse_rss(xml_text: str) -> list[dict]:
    """Parse RSS XML into a list of note records."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"WARNING: RSS XML parse error: {e}", file=sys.stderr)
        return []

    items = root.findall(".//item")
    notes = []
    for item in items:
        record = parse_rss_item(item)
        if record:
            notes.append(record)
    return notes


def load_cache(cache_path: Path) -> dict[str, list[dict]]:
    """Load existing cumulative cache (keyed by iWine)."""
    if cache_path.exists() and cache_path.stat().st_size > 0:
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
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


def fetch_community_notes(rss_url: str, cache_path: Path) -> None:
    """Fetch RSS feed, parse, merge into cumulative cache."""
    # Fetch RSS
    try:
        req = urllib.request.Request(
            rss_url, headers={"User-Agent": "The-Sommelier/1.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            xml_text = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        # The error text can hold the URL, and the URL holds the CK token. The
        # HTTP status is safe, and it tells a bad token (401) from an outage.
        status = f" {e.code}" if isinstance(e, urllib.error.HTTPError) else ""
        print(f"ERROR: RSS fetch failed ({type(e).__name__}{status})", file=sys.stderr)
        sys.exit(1)

    # Parse
    new_notes = parse_rss(xml_text)
    if not new_notes:
        print("No notes found in RSS feed.")
        return

    # Merge
    cache = load_cache(cache_path)
    cache, added = merge_notes(cache, new_notes)

    # Write atomically (tmp + rename) to protect cumulative cache from partial writes
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(".tmp")
    try:
        tmp_path.write_text(
            json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        tmp_path.replace(cache_path)
    except OSError as e:
        print(f"ERROR: Failed to write community notes cache: {e}", file=sys.stderr)
        tmp_path.unlink(missing_ok=True)
        sys.exit(1)

    total = sum(len(v) for v in cache.values())
    wines = len(cache)
    print(f"Community notes: {added} new, {total} total across {wines} wines")


if __name__ == "__main__":
    url = settings.community_notes_url()
    if not url:
        print(
            "ERROR: CT_COMMUNITY_NOTES_RSS environment variable not set",
            file=sys.stderr,
        )
        sys.exit(1)

    cache = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else settings.data_dir() / "community_notes.json"
    )
    fetch_community_notes(url, cache)
