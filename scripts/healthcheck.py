#!/usr/bin/env python3
"""Send one Healthchecks.io ping.

Usage: healthcheck.py [--digest] success|fail <message>

Without --digest, the ping goes to the sync check (HEALTHCHECK_URL). With
--digest, the ping goes to the digest check (HEALTHCHECK_DIGEST_URL) when that
feature is on. When it is off, a fail goes to the sync check, and a success
sends no ping. Thus a host with no digest check gets the same alerts as before.

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


def ping(status: str, message: str, check: str = "sync") -> bool:
    """POST the message. Return True when Healthchecks.io accepts it or the feature is off."""
    url = settings.healthcheck_url(status, check)
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
    digest = argv[:1] == ["--digest"]
    if digest:
        argv = argv[1:]
    if len(argv) != 2 or argv[0] not in ("success", "fail"):
        print(
            "Usage: healthcheck.py [--digest] success|fail <message>", file=sys.stderr
        )
        return 2
    status, message = argv
    if not digest:
        ping(status, message)
    elif settings.feature("healthcheck_digest").on:
        ping(status, message, check="digest")
    elif status == "fail":
        ping(status, message)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
