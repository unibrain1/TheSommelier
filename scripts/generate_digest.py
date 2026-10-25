#!/usr/bin/env python3
"""Generate a morning digest for tonight's wine and menu pairings.

Reads site/plan.json and site/pairing_suggestions.json. Produces
site/digest.json (structured) and site/digest.html (branded email).

Only generates content when there's a menu entry for today — this is
a "go pull this wine" reminder, not a daily newsletter.

Exit codes:
  0 — digest generated with content worth sending
  2 — nothing to send (no menu today)

Usage: generate_digest.py [--force]
"""

import base64
import html
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Ensure TZ env var is respected
if "TZ" in os.environ:
    time.tzset()

from wine_utils import find_current_week

LOCAL_TZ = ZoneInfo("America/Los_Angeles")


def _today_local() -> date:
    return datetime.now(LOCAL_TZ).date()


def find_meals_for_date(suggestions: list[dict], target: str) -> list[dict]:
    """Find all pairing suggestions for a specific date."""
    return [s for s in suggestions if s.get("date") == target]


def build_digest(
    plan_path: Path,
    pairing_path: Path,
) -> dict:
    """Build the digest data structure."""
    today = _today_local()
    today_str = today.isoformat()

    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
    all_weeks = plan_data.get("allWeeks", [])

    pairing_data = json.loads(pairing_path.read_text(encoding="utf-8"))
    suggestions = pairing_data.get("suggestions", [])

    current_week = find_current_week(all_weeks, today)
    today_pairings = find_meals_for_date(suggestions, today_str)

    has_content = len(today_pairings) > 0

    # Find tonight's top suggestion (the actionable "go pull this bottle" item)
    tonight_suggestion = None
    for p in today_pairings:
        sb = p.get("suggested_bottle")
        if sb:
            tonight_suggestion = {
                "meal": p.get("meal", ""),
                "bottle": sb,
                "pairing": p.get("pairing", {}),
            }
            break

    digest: dict = {
        "date": today_str,
        "date_display": today.strftime("%A, %B %-d, %Y"),
        "has_content": has_content,
        "wine": None,
        "tonight": tonight_suggestion,
        "pairings": today_pairings,
    }

    if current_week:
        digest["wine"] = {
            "name": current_week.get("name", ""),
            "vintage": current_week.get("vintage", ""),
            "badge": current_week.get("badge", ""),
            "note": current_week.get("note", ""),
            "window": current_week.get("window"),
            "score": current_week.get("score"),
            "location": current_week.get("location", ""),
            "urgent": current_week.get("urgent", False),
        }

    return digest


def _badge_color(badge: str) -> str:
    colors = {
        "sparkling": "#29abe0",
        "rose": "#e83e8c",
        "white": "#c49b2a",
        "red": "#8b3a3a",
    }
    return colors.get(badge, "#8b3a3a")


def _badge_icon(badge: str) -> str:
    icons = {"sparkling": "🥂", "rose": "🌹", "white": "🥃", "red": "🍷"}
    return icons.get(badge, "🍷")


def _pairing_color(score: str) -> str:
    return {"good": "#5cb85c", "partial": "#f0ad4e", "poor": "#d9534f"}.get(
        score, "#dfd7ca"
    )


def _logo_src(site_dir: Path) -> str:
    """Return the logo as an inline data URI, a SITE_URL link, or empty."""
    logo_path = site_dir / "logo.png"
    if logo_path.exists():
        encoded = base64.b64encode(logo_path.read_bytes()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    site_url = os.environ.get("SITE_URL", "").strip()
    if site_url.startswith(("http://", "https://")):
        return html.escape(site_url.rstrip("/") + "/logo.png", quote=True)
    print(
        "WARNING: site/logo.png not found and SITE_URL is not an http(s) URL. "
        "The digest has no logo.",
        file=sys.stderr,
    )
    return ""


def format_digest_html(digest: dict, logo_src: str = "") -> str:
    """Format the digest as a branded HTML email.

    Structure: tonight's dinner recommendation first (action item),
    then this week's planned wine (context).
    """
    wine = digest.get("wine")
    tonight = digest.get("tonight")
    pairings = digest.get("pairings", [])
    date_display = digest.get("date_display", digest["date"])

    # Tonight's dinner section (primary — the action item)
    tonight_html = ""
    if tonight:
        sb = tonight["bottle"]
        meal = tonight["meal"]
        pairing = tonight["pairing"]
        score = pairing.get("score", "neutral")
        color = _pairing_color(score)
        status_icon = {"good": "✅", "partial": "🟡", "poor": "❌"}.get(score, "—")

        sb_badge = sb.get("type", "red").lower()
        if "rose" in sb_badge or "rosé" in sb_badge:
            sb_badge = "rose"
        elif "sparkling" in sb_badge:
            sb_badge = "sparkling"
        elif "white" in sb_badge:
            sb_badge = "white"
        else:
            sb_badge = "red"
        badge_color = _badge_color(sb_badge)
        badge_icon = _badge_icon(sb_badge)

        sb_meta_parts = []
        if sb.get("window"):
            sb_meta_parts.append(f"Window: {sb['window']}")
        if sb.get("urgency"):
            sb_meta_parts.append(sb["urgency"])
        if sb.get("location"):
            sb_meta_parts.append(f"Location: {sb['location']}")
        sb_meta = " &nbsp;·&nbsp; ".join(sb_meta_parts)

        urgent_html = ""
        if sb.get("urgency") and "past peak" in sb.get("urgency", "").lower():
            urgent_html = '<div style="color:#d9534f;font-size:13px;font-weight:600;margin-top:6px;">⚠ Past peak — drink now</div>'

        tonight_html = f"""
<tr><td style="padding:28px 32px 0;">
  <div style="font-size:11px;text-transform:uppercase;letter-spacing:1.5px;color:#325d88;font-weight:600;margin-bottom:10px;">🍽 Pull for Tonight</div>
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#e8eef4;border-radius:8px;border:1px solid #c5d5e4;">
  <tr><td style="padding:20px 24px;">
    <table cellpadding="0" cellspacing="0"><tr>
      <td style="vertical-align:top;padding-right:14px;">
        <div style="width:36px;height:36px;border-radius:50%;background:{badge_color};text-align:center;line-height:36px;font-size:16px;">{badge_icon}</div>
      </td>
      <td>
        <div style="font-size:20px;font-weight:600;color:#3e3f3a;">{sb.get("vintage")} {sb.get("wine")}</div>
        <div style="color:#8e8c84;font-size:14px;margin-top:4px;">{sb_meta}</div>
        {urgent_html}
        <div style="margin-top:10px;padding-top:10px;border-top:1px solid #c5d5e4;">
          <div style="font-size:14px;color:#3e3f3a;"><strong>Dinner:</strong> {meal}</div>
          <div style="color:{color};font-size:13px;margin-top:3px;">{status_icon} Planned wine is a {score} match — the sommelier suggests this instead</div>
        </div>
      </td>
    </tr></table>
  </td></tr>
  </table>
</td></tr>"""
    elif pairings:
        # No suggestion — planned wine pairs well, show the meal
        p = pairings[0]
        meal = p.get("meal", "")
        pairing = p.get("pairing", {})
        score = pairing.get("score", "neutral")
        status_icon = {"good": "✅", "partial": "🟡", "poor": "❌"}.get(score, "—")
        tonight_html = f"""
<tr><td style="padding:28px 32px 0;">
  <div style="font-size:11px;text-transform:uppercase;letter-spacing:1.5px;color:#325d88;font-weight:600;margin-bottom:10px;">🍽 Tonight's Dinner</div>
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f7e6;border-radius:8px;border:1px solid #c5dba8;">
  <tr><td style="padding:16px 20px;">
    <div style="font-size:16px;font-weight:500;">{meal}</div>
    <div style="color:#5cb85c;font-size:13px;margin-top:4px;">{status_icon} The planned wine pairs well — no swap needed</div>
  </td></tr>
  </table>
</td></tr>"""

    # This week's wine section (secondary — context)
    wine_html = ""
    if wine:
        badge_color = _badge_color(wine["badge"])
        badge_icon = _badge_icon(wine["badge"])
        urgent_html = ""
        if wine.get("urgent"):
            urgent_html = '<div style="color:#d9534f;font-size:13px;font-weight:600;margin-top:6px;">⚠ Past peak — drink now</div>'

        meta_parts = []
        if wine.get("window"):
            meta_parts.append(f"Window: {wine['window']}")
        if wine.get("score"):
            meta_parts.append(f"Score: {wine['score']}")
        if wine.get("location"):
            meta_parts.append(f"Location: {wine['location']}")
        meta_html = " &nbsp;·&nbsp; ".join(meta_parts)

        note_html = ""
        if wine.get("note"):
            note_html = f'<div style="color:#3e3f3a;font-size:14px;margin-top:8px;line-height:1.5;font-style:italic;">{wine["note"]}</div>'

        wine_html = f"""
<tr><td style="padding:20px 32px 0;">
  <div style="font-size:11px;text-transform:uppercase;letter-spacing:1.5px;color:#8e8c84;font-weight:600;margin-bottom:10px;">This Week's Selection</div>
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8f5f0;border-radius:8px;border:1px solid #dfd7ca;">
  <tr><td style="padding:16px 20px;">
    <table cellpadding="0" cellspacing="0"><tr>
      <td style="vertical-align:top;padding-right:12px;">
        <div style="width:28px;height:28px;border-radius:50%;background:{badge_color};text-align:center;line-height:28px;font-size:13px;">{badge_icon}</div>
      </td>
      <td>
        <div style="font-size:16px;font-weight:600;color:#3e3f3a;">{wine["vintage"]} {wine["name"]}</div>
        <div style="color:#8e8c84;font-size:13px;margin-top:3px;">{meta_html}</div>
        {urgent_html}
        {note_html}
      </td>
    </tr></table>
  </td></tr>
  </table>
</td></tr>"""

    logo_html = (
        f'<img src="{logo_src}" alt="The Sommelier" width="120" style="display:block;margin:0 auto 12px;">'
        if logo_src
        else ""
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f8f5f0;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;color:#3e3f3a;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8f5f0;padding:20px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">

<tr><td style="background:linear-gradient(135deg,#3e3f3a 0%,#4a4b46 100%);padding:28px 32px;text-align:center;">
  {logo_html}
  <div style="color:#c5bdb0;font-size:13px;margin-top:6px;letter-spacing:1px;text-transform:uppercase;">
    {date_display}
  </div>
</td></tr>

{tonight_html}
{wine_html}

<tr><td style="padding:28px 32px;text-align:center;">
  <div style="color:#c5bdb0;font-size:12px;">
    The Sommelier · Drink the right bottles at the right time
  </div>
</td></tr>

</table>
</td></tr>
</table>
</body></html>"""


def main() -> None:
    force = "--force" in sys.argv

    plan_path = Path("site/plan.json")
    pairing_path = Path("site/pairing_suggestions.json")

    if not plan_path.exists():
        print("ERROR: site/plan.json not found", file=sys.stderr)
        sys.exit(1)
    if not pairing_path.exists():
        print("ERROR: site/pairing_suggestions.json not found", file=sys.stderr)
        sys.exit(1)

    digest = build_digest(plan_path, pairing_path)
    logo_src = _logo_src(Path("site"))
    digest_html = format_digest_html(digest, logo_src=logo_src)

    json_path = Path("site/digest.json")
    html_path = Path("site/digest.html")
    try:
        json_path.write_text(
            json.dumps(digest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        html_path.write_text(digest_html, encoding="utf-8")
    except OSError as e:
        # Clean up partial writes so send_digest.py doesn't find stale files
        json_path.unlink(missing_ok=True)
        html_path.unlink(missing_ok=True)
        print(f"ERROR: Failed to write digest files: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Digest generated for {digest['date']}")

    if not digest["has_content"] and not force:
        print("  No menu today — nothing to send")
        sys.exit(2)

    print(f"  {len(digest['pairings'])} meal(s) on the menu")


if __name__ == "__main__":
    main()
