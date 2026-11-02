#!/usr/bin/env python3
"""Send the morning digest via email (SMTP, Gmail by default).

Reads digest.json and digest.html from the site folder (SITE_DIR), sends via
SMTP with STARTTLS. Idempotent: tracks last-sent date in the data folder
(DATA_DIR) to prevent double-sends. SMTP settings come from settings.py.

Usage: send_digest.py [--dry-run] [--force]
  --dry-run  Print the email subject and recipient list without sending
  --force    Send even if digest has no content or already sent today
"""

import json
import smtplib
import ssl
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr
from pathlib import Path
from zoneinfo import ZoneInfo

import settings

LOCAL_TZ = ZoneInfo("America/Los_Angeles")


def _today_local() -> str:
    """Return today's date in Pacific time as ISO string."""
    return datetime.now(LOCAL_TZ).date().isoformat()


def _state_file() -> Path:
    """Return the file that holds the last-sent date."""
    return settings.data_dir() / "digest_last_sent.txt"


def already_sent_today() -> bool:
    """Check if digest was already sent today (Pacific time)."""
    state_file = _state_file()
    if not state_file.exists():
        return False
    last_sent = state_file.read_text().strip()
    return last_sent == _today_local()


def mark_sent() -> None:
    """Record that digest was sent today (Pacific time)."""
    state_file = _state_file()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(_today_local())


def build_email(
    digest: dict, html_body: str, sender: str, recipients: list[str]
) -> MIMEMultipart:
    """Build the email message with HTML body."""
    tonight = digest.get("tonight")
    wine = digest.get("wine")
    if tonight:
        sb = tonight["bottle"]
        subject = f"Tonight: pull {sb.get('vintage')} {sb.get('wine')}"
    elif wine:
        subject = f"Tonight: {wine['vintage']} {wine['name']}"
    else:
        subject = "The Sommelier — Daily Digest"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    return msg


def send_email(msg: MIMEMultipart, smtp_settings: settings.SmtpSettings) -> list[str]:
    """Send via SMTP with STARTTLS. Return the recipients that the server refused.

    smtplib raises only when the server refuses all recipients. When it refuses
    some, send_message returns them, and the others get the email.
    """
    if smtp_settings.port is None:
        raise ValueError("SMTP_PORT is not valid")
    with smtplib.SMTP(smtp_settings.host, smtp_settings.port) as server:
        # Without a context, starttls() does not check the certificate or the
        # host name, so any server on the path could read the password.
        server.starttls(context=ssl.create_default_context())
        server.login(smtp_settings.username, smtp_settings.password)
        return sorted(server.send_message(msg))


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv

    state = settings.feature("digest")
    if state.kind == "disabled":
        print(f"Digest off ({state.reason})")
        return
    # The documented rule for a flag that is not valid is "off, with a
    # warning", so the scheduled job must not fail every day on it.
    if state.kind == "bad_flag":
        print(f"WARNING: Digest off ({state.reason})", file=sys.stderr)
        return
    # No digest settings at all is a normal state (settings.py check logs it),
    # so the scheduled job must not fail on it. Partial settings still fail.
    if not dry_run and state.kind == "not_configured":
        print("Digest email not configured — nothing to send")
        return

    site_dir = settings.site_dir()
    digest_json = site_dir / "digest.json"
    digest_html = site_dir / "digest.html"

    if not digest_json.exists():
        print(
            f"ERROR: {digest_json} not found — run generate_digest.py first",
            file=sys.stderr,
        )
        sys.exit(1)

    digest = json.loads(digest_json.read_text(encoding="utf-8"))

    if not digest_html.exists():
        print(f"ERROR: {digest_html} not found", file=sys.stderr)
        sys.exit(1)
    html_body = digest_html.read_text(encoding="utf-8")

    # Check if digest is for today (guard against stale data from failed pipeline)
    today_str = _today_local()
    if digest.get("date") != today_str and not force:
        print(
            f"Digest is for {digest.get('date')}, not today ({today_str}). "
            "Use --force to override."
        )
        return

    # Check if there's content worth sending
    if not digest.get("has_content") and not force:
        print("Nothing to send (no menu today). Use --force to override.")
        return

    # Idempotency check
    if already_sent_today() and not force:
        print(f"Digest already sent today ({today_str}). Use --force to resend.")
        return

    if not dry_run and not state.on:
        print(
            "ERROR: Digest email is not configured. Missing or not valid: "
            + ", ".join(state.problems),
            file=sys.stderr,
        )
        sys.exit(1)

    smtp_settings = settings.smtp()
    recipients = smtp_settings.recipients

    msg = build_email(digest, html_body, smtp_settings.sender, recipients)

    if dry_run:
        print("=== DRY RUN ===")
        print(f"From: {smtp_settings.sender or '(not set)'}")
        print(f"To: {', '.join(recipients) if recipients else '(no recipients)'}")
        print(f"Subject: {msg['Subject']}")
        print(f"Body: {len(html_body)} bytes HTML")
        print("=== END DRY RUN ===")
        return

    try:
        refused = send_email(msg, smtp_settings)
    except (smtplib.SMTPException, OSError) as e:
        print(f"ERROR: Failed to send digest email: {e}", file=sys.stderr)
        sys.exit(1)

    # The other recipients got the email, so a resend would duplicate it.
    mark_sent()
    # smtplib gives the refused recipients as bare addresses, without a name.
    sent = [r for r in recipients if parseaddr(r)[1] not in refused]
    print(f"Digest sent to {', '.join(sent)}")
    if refused:
        print(f"WARNING: The server refused: {', '.join(refused)}", file=sys.stderr)


if __name__ == "__main__":
    main()
