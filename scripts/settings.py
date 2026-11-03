#!/usr/bin/env python3
"""Settings from plain environment variables — the one place for the rules.

The host injects the values (compose env_file, op run, Ansible vault). Python
scripts import the functions. Shell scripts use the CLI, because the security
rules forbid eval:

  settings.py check              Log the state of each feature. Exit 1 when a
                                 required variable is missing.
  settings.py enabled <feature>  Exit 0 when the feature is on, else 1.
  settings.py get <name>         Print one value (data_dir, site_dir).

A bad argument exits 2. DATA_DIR defaults to /data, SITE_DIR defaults to
$DATA_DIR/site, and PLAN_CONFIG defaults to /config/plan.yaml.

Each function reads os.environ when it is called, not at import, so tests can
change the environment with monkeypatch.
"""

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

REQUIRED = ("USERNAME", "PASSWORD", "GOOGLE_CALENDAR_ICS_URL")
ANTHROPIC_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

DEFAULT_DATA_DIR = "/data"
DEFAULT_PLAN_CONFIG = "/config/plan.yaml"
DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = 587
DEFAULT_HC_SLUG = "the-sommelier"
DEFAULT_HC_BASE_URL = "https://hc-ping.com"

# feature -> (enable flag, settings that must be present)
FEATURES: dict[str, tuple[str, tuple[str, ...]]] = {
    "digest": (
        "DIGEST_ENABLED",
        ("SMTP_USERNAME", "SMTP_PASSWORD", "DIGEST_RECIPIENTS"),
    ),
    "claude": ("CLAUDE_ENABLED", ("CLAUDE_CODE_OAUTH_TOKEN",)),
    "community_notes": ("COMMUNITY_NOTES_ENABLED", ("CT_COMMUNITY_NOTES_RSS",)),
    "healthcheck": ("HEALTHCHECK_ENABLED", ("HEALTHCHECK_PING_KEY",)),
    # Home Assistant pulls the two JSON files, so the app needs no credentials.
    # With no settings to check, only the flag can turn the feature on.
    "home_assistant": ("HA_ENABLED", ()),
}

# Every variable that this module reads. Tests clear these before each case.
ALL_VARS: tuple[str, ...] = (
    *REQUIRED,
    *(v for flag, needed in FEATURES.values() for v in (flag, *needed)),
    "DATA_DIR",
    "SITE_DIR",
    "PLAN_CONFIG",
    "SITE_URL",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_FROM",
    "HEALTHCHECK_SLUG",
    "HEALTHCHECK_BASE_URL",
    *ANTHROPIC_VARS,
)

# The key and the slug are path parts of the ping URL. "/", "?" or "#" would move
# the key or the /fail suffix into another part of the URL.
_URL_PART = re.compile(r"[A-Za-z0-9_-]+")

_TRUE = {"true", "1", "yes"}
_FALSE = {"false", "0", "no"}

# Optional values that are not feature settings. An op:// value here would
# become a wrong path, host or address, with no other message.
_PATH_VARS = ("DATA_DIR", "SITE_DIR", "PLAN_CONFIG")
_OTHER_VARS = ("SMTP_HOST", "SMTP_FROM", "SITE_URL")

# The state classes of the plan. Callers test kind, not the reason text, so a
# change to the log text cannot change what a caller does.
Kind = Literal["on", "disabled", "not_configured", "bad_flag", "incomplete"]


@dataclass(frozen=True)
class FeatureState:
    name: str
    kind: Kind
    reason: str = ""
    # Names of variables that are missing or not valid. Never values.
    problems: list[str] = field(default_factory=list)

    @property
    def on(self) -> bool:
        return self.kind == "on"

    def log_line(self) -> str:
        if self.on:
            return f"{self.name}: on"
        return f"{self.name}: off ({self.reason})"


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int | None
    username: str
    password: str = field(repr=False)
    sender: str
    recipients: list[str]


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _unresolved(value: str) -> bool:
    """An op:// reference means the host did not resolve the secret."""
    return value.startswith("op://")


def _var_unresolved(var: str) -> bool:
    # DIGEST_RECIPIENTS is a list, so one op:// entry can follow a real address.
    if var == "DIGEST_RECIPIENTS":
        return any(_unresolved(r) for r in _recipients())
    return _unresolved(_env(var))


def data_dir() -> Path:
    return Path(_env("DATA_DIR") or DEFAULT_DATA_DIR)


def site_dir() -> Path:
    value = _env("SITE_DIR")
    return Path(value) if value else data_dir() / "site"


def plan_config_path() -> Path:
    return Path(_env("PLAN_CONFIG") or DEFAULT_PLAN_CONFIG)


def required() -> list[str]:
    """Return the names of required variables that are missing or unresolved."""
    return [n for n in REQUIRED if not _env(n) or _unresolved(_env(n))]


def _recipients() -> list[str]:
    return [r.strip() for r in _env("DIGEST_RECIPIENTS").split(",") if r.strip()]


def _smtp_port() -> int | None:
    raw = _env("SMTP_PORT")
    if not raw:
        return DEFAULT_SMTP_PORT
    # isdigit alone accepts "²", which int() rejects.
    if not (raw.isascii() and raw.isdigit()):
        return None
    port = int(raw)
    return port if 1 <= port <= 65535 else None


def _hc_base_url() -> str | None:
    base = (_env("HEALTHCHECK_BASE_URL") or DEFAULT_HC_BASE_URL).rstrip("/")
    try:
        parts = urlsplit(base)
        parts.port  # noqa: B018 — raises ValueError for a port out of range
    except ValueError:
        return None
    # A query or a fragment, even an empty one, would swallow the key and the slug.
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.port == 0
        or "?" in base
        or "#" in base
    ):
        return None
    return base


def _hc_slug() -> str | None:
    slug = _env("HEALTHCHECK_SLUG") or DEFAULT_HC_SLUG
    return slug if _URL_PART.fullmatch(slug) else None


def _invalid_values(name: str) -> list[str]:
    """Variables of a feature that are set but have a value that is not valid."""
    problems = []
    if name == "digest":
        if _smtp_port() is None:
            problems.append("SMTP_PORT")
        problems += [v for v in ("SMTP_HOST", "SMTP_FROM") if _unresolved(_env(v))]
    elif name == "healthcheck":
        key = _env("HEALTHCHECK_PING_KEY")
        if key and not _unresolved(key) and not _URL_PART.fullmatch(key):
            problems.append("HEALTHCHECK_PING_KEY")
        if _hc_base_url() is None:
            problems.append("HEALTHCHECK_BASE_URL")
        if _hc_slug() is None:
            problems.append("HEALTHCHECK_SLUG")
    return problems


def _present(var: str) -> bool:
    if var == "DIGEST_RECIPIENTS":
        return bool(_recipients())
    return bool(_env(var))


def feature(name: str) -> FeatureState:
    """Apply the on/off rule to one feature."""
    if name not in FEATURES:
        raise ValueError(f"unknown feature: {name}")
    flag, needed = FEATURES[name]

    flag_value = _env(flag).lower()
    if flag in os.environ and flag_value not in _TRUE | _FALSE:
        # An unknown value fails safe: the feature stays off.
        return FeatureState(name, "bad_flag", f"not valid: {flag}", [flag])
    if flag_value in _FALSE:
        return FeatureState(name, "disabled", f"disabled by {flag}")
    if not needed:
        if flag_value in _TRUE:
            return FeatureState(name, "on")
        return FeatureState(name, "not_configured", f"set {flag}=true to turn on")

    present = [v for v in needed if _present(v)]
    if not present:
        return FeatureState(name, "not_configured", "not configured")

    problems = [v for v in needed if not _present(v) or _var_unresolved(v)]
    problems += _invalid_values(name)
    if problems:
        return FeatureState(
            name, "incomplete", "missing or not valid: " + ", ".join(problems), problems
        )
    return FeatureState(name, "on")


def smtp() -> SmtpSettings:
    """Return the SMTP values. Port is None when SMTP_PORT is not valid."""
    username = _env("SMTP_USERNAME")
    return SmtpSettings(
        host=_env("SMTP_HOST") or DEFAULT_SMTP_HOST,
        port=_smtp_port(),
        username=username,
        password=_env("SMTP_PASSWORD"),
        sender=_env("SMTP_FROM") or username,
        recipients=_recipients(),
    )


def healthcheck_url(status: str = "success") -> str | None:
    """Return <base>/<key>/<slug> (plus /fail), or None when the feature is off."""
    if not feature("healthcheck").on:
        return None
    url = f"{_hc_base_url()}/{_env('HEALTHCHECK_PING_KEY')}/{_hc_slug()}"
    return url + "/fail" if status == "fail" else url


def community_notes_url() -> str:
    return _env("CT_COMMUNITY_NOTES_RSS")


def site_url() -> str:
    return _env("SITE_URL")


OP_WARNING = (
    "WARNING: A value starts with op://. "
    "Resolve it before the start, for example with op run."
)


def check_lines() -> tuple[list[str], list[str], list[str]]:
    """Return (info lines, warnings, errors). No line holds a value."""
    states = [feature(n) for n in FEATURES]
    info = ["Settings:"] + [f"    {state.log_line()}" for state in states]
    warnings = []
    for state in states:
        n = state.name
        if state.problems:
            warnings.append(
                f"WARNING: {n} is off. Missing or not valid: {', '.join(state.problems)}"
            )
            if any(_var_unresolved(v) for v in state.problems):
                warnings.append(OP_WARNING)
    for var in _OTHER_VARS:
        if _unresolved(_env(var)) and not any(var in s.problems for s in states):
            warnings.append(f"WARNING: {var} is not resolved. It is not used.")
            warnings.append(OP_WARNING)
    for var in ANTHROPIC_VARS:
        if _env(var):
            warnings.append(
                f"WARNING: {var} is set. The claude CLI uses it before "
                "CLAUDE_CODE_OAUTH_TOKEN, so the runs go on the API bill, "
                "not the subscription."
            )
    errors = [
        f"ERROR: Required variable {n} is missing or not resolved" for n in required()
    ]
    errors += [
        f"ERROR: {n} is not resolved. Set a path."
        for n in _PATH_VARS
        if _unresolved(_env(n))
    ]
    return info, warnings, errors


def _main(argv: list[str]) -> int:
    if len(argv) >= 1 and argv[0] == "check":
        info, warnings, errors = check_lines()
        for line in info:
            print(line)
        for line in warnings + errors:
            print(line, file=sys.stderr)
        return 1 if errors else 0
    if len(argv) == 2 and argv[0] == "enabled":
        if argv[1] not in FEATURES:
            print(f"ERROR: Unknown feature: {argv[1]}", file=sys.stderr)
            return 2
        return 0 if feature(argv[1]).on else 1
    if len(argv) == 2 and argv[0] == "get":
        getters = {"data_dir": data_dir, "site_dir": site_dir}
        if argv[1] not in getters:
            print(f"ERROR: Unknown setting: {argv[1]}", file=sys.stderr)
            return 2
        print(getters[argv[1]]())
        return 0
    print(
        "Usage: settings.py check | enabled <feature> | get <data_dir|site_dir>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
