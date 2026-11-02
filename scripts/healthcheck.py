#!/usr/bin/env python3
"""Send one Healthchecks.io ping.

Usage: healthcheck.py success|fail <message>

The URL holds the ping key, so it stays out of command arguments (ps) and out
of every message. urllib errors can hold the URL, so a failure prints a fixed
text. A failed ping never stops the pipeline: the exit code is 0. A bad
argument exits 2.
"""

import sys
import time
import urllib.request

import settings

TIMEOUT_SECONDS = 10
TRIES = 3
RETRY_DELAY_SECONDS = 1


def ping(status: str, message: str) -> bool:
    """POST the message. Return True when Healthchecks.io accepts it."""
    url = settings.healthcheck_url(status)
    if url is None:
        return True
    data = message.encode("utf-8")
    for attempt in range(TRIES):
        try:
            request = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS):
                return True
        except Exception:  # noqa: BLE001 — the text can hold the key
            if attempt + 1 < TRIES:
                time.sleep(RETRY_DELAY_SECONDS)
    print("WARNING: Failed to send Healthchecks.io ping", file=sys.stderr)
    return False


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ("success", "fail"):
        print("Usage: healthcheck.py success|fail <message>", file=sys.stderr)
        return 2
    ping(argv[0], argv[1])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
