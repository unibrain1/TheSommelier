#!/usr/bin/env python3
"""Docker HEALTHCHECK for the container.

Exit 0 when nginx serves the site and the last complete run is recent. Else
exit 1 and print one line that names the cause.

The pipeline publishes $SITE_DIR/plan.json only after all fatal steps, so its
mtime is the time of the last complete run. No other state file is necessary.
"""

import http.client
import stat
import sys
import time
import urllib.error
import urllib.request

import settings

NGINX_INDEX_URL = "http://127.0.0.1:8080/index.html"
TIMEOUT_SECONDS = 5


def check_site(url: str) -> str | None:
    """Return the cause when the URL does not give 200, else None."""
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code
    except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
        # A reply that is not HTTP can put line breaks in the error text.
        reason = " ".join(str(error).split())
        return f"UNHEALTHY: {url} did not respond ({reason})"
    if status != 200:
        return f"UNHEALTHY: {url} gave HTTP {status}"
    return None


def check_plan() -> str | None:
    """Return the cause when plan.json is missing or too old, else None."""
    plan = settings.site_dir() / "plan.json"
    limit = settings.health_max_age_minutes()
    try:
        info = plan.stat()
    except FileNotFoundError:
        return f"UNHEALTHY: {plan} is missing. No run completed."
    except OSError as error:
        return f"UNHEALTHY: cannot read {plan} ({error})"
    # nginx cannot serve a folder, so a folder in place of plan.json is not healthy.
    if not stat.S_ISREG(info.st_mode):
        return f"UNHEALTHY: {plan} is not a file."
    age_seconds = time.time() - info.st_mtime
    # Compare seconds, so 600 minutes and 30 seconds is too old for a limit of 600.
    if age_seconds > limit * 60:
        age = int(age_seconds // 60)
        return f"UNHEALTHY: {plan} is {age} minutes old. The limit is {limit}."
    return None


def main(url: str = NGINX_INDEX_URL) -> int:
    cause = check_site(url) or check_plan()
    if cause:
        print(cause)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
