#!/usr/bin/env python3
"""Send one Healthchecks.io ping.

Usage: healthcheck.py success|fail <message>

The URL holds the ping key, so it stays out of command arguments (ps) and out
of every message. Some urllib errors repeat the URL (for example InvalidURL and
ValueError), so a failure prints only the exception class, plus the status code
for an HTTPError. A failed ping never stops the pipeline: the exit code is 0. A
bad argument exits 2.
"""

import sys
import time
import urllib.error
import urllib.request

import settings

TIMEOUT_SECONDS = 10
TRIES = 3
RETRY_DELAY_SECONDS = 1


def ping(status: str, message: str) -> bool:
    """POST the message. Return True when Healthchecks.io accepts it or the feature is off."""
    url = settings.healthcheck_url(status)
    if url is None:
        return True
    data = message.encode("utf-8")
    reason = ""
    for attempt in range(TRIES):
        try:
            request = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS):
                return True
        # Any failure, a bad URL included, is only a missed ping. Do not print
        # the exception text, because it can hold the key.
        except Exception as e:  # noqa: BLE001
            code = f" HTTP {e.code}" if isinstance(e, urllib.error.HTTPError) else ""
            reason = f"{type(e).__name__}{code}"
            if attempt + 1 < TRIES:
                time.sleep(RETRY_DELAY_SECONDS)
    print(f"WARNING: Failed to send Healthchecks.io ping ({reason})", file=sys.stderr)
    return False


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ("success", "fail"):
        print("Usage: healthcheck.py success|fail <message>", file=sys.stderr)
        return 2
    ping(argv[0], argv[1])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
