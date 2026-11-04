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
HEALTH_MAX_AGE_MINUTES defaults to 600. A value that is not a positive integer
gives the default, and check logs a warning.

A feature turns on when all its settings are present and valid. Only
home_assistant has a <FEATURE>_ENABLED flag. It has no settings, so it turns
on only when its flag is a true value. The claude feature turns on when
exactly one of ANTHROPIC_API_KEY (API key mode) and CLAUDE_CODE_OAUTH_TOKEN
(subscription mode) is set.

Each function reads os.environ when it is called, not at import, so tests can
change the environment with monkeypatch.
"""

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

REQUIRED = ("USERNAME", "PASSWORD", "GOOGLE_CALENDAR_ICS_URL")

# The two Claude keys, one for each mode. Exactly one is allowed, so the
# choice of bill is explicit and not left to the CLI.
CLAUDE_API_KEY = "ANTHROPIC_API_KEY"
CLAUDE_OAUTH_TOKEN = "CLAUDE_CODE_OAUTH_TOKEN"
# Not a mode. The subprocess must never get it.
UNUSED_ANTHROPIC_VAR = "ANTHROPIC_AUTH_TOKEN"
CLAUDE_CREDENTIALS = (CLAUDE_API_KEY, UNUSED_ANTHROPIC_VAR, CLAUDE_OAUTH_TOKEN)

# The SSH push to Home Assistant was removed in v4.0.0. A host that still sets
# this variable would otherwise see frozen sensors with no signal.
REMOVED_HA_VAR = "HA_SSH_TARGET"
# removed flag -> its feature. The settings alone turn these features on. A
# host that still sets a flag to false would otherwise get a feature it thinks
# is off, with no signal.
REMOVED_FLAGS: dict[str, str] = {
    "CLAUDE_ENABLED": "claude",
    "DIGEST_ENABLED": "digest",
    "COMMUNITY_NOTES_ENABLED": "community_notes",
}

DEFAULT_DATA_DIR = "/data"
DEFAULT_PLAN_CONFIG = "/config/plan.yaml"
DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = 587
# The 8-hour night gap of the default schedule, plus 2 hours.
DEFAULT_HEALTH_MAX_AGE_MINUTES = 600

# feature -> (enable flag, settings that must be present). A feature with no
# flag is on when its settings are. The claude feature has its own rule in
# _claude_state().
FEATURES: dict[str, tuple[str | None, tuple[str, ...]]] = {
    "digest": (None, ("SMTP_USERNAME", "SMTP_PASSWORD", "DIGEST_RECIPIENTS")),
    "claude": (None, (CLAUDE_API_KEY, CLAUDE_OAUTH_TOKEN)),
    "community_notes": (None, ("CT_COMMUNITY_NOTES_RSS",)),
    "healthcheck": (None, ("HEALTHCHECK_URL",)),
    # Home Assistant pulls the two JSON files, so the app needs no credentials.
    # With no settings to check, only the flag can turn the feature on.
    "home_assistant": ("HA_ENABLED", ()),
}

# Every variable that this module reads. Tests clear these before each case.
ALL_VARS: tuple[str, ...] = (
    *REQUIRED,
    *(v for flag, needed in FEATURES.values() for v in (flag, *needed) if v),
    "DATA_DIR",
    "SITE_DIR",
    "PLAN_CONFIG",
    "SITE_URL",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_FROM",
    "HEALTH_MAX_AGE_MINUTES",
    UNUSED_ANTHROPIC_VAR,
    REMOVED_HA_VAR,
    *REMOVED_FLAGS,
)

_TRUE = {"true", "1", "yes"}
_FALSE = {"false", "0", "no"}

# Optional values that are not feature settings. An op:// value here would
# become a wrong path, host or address, with no other message.
_PATH_VARS = ("DATA_DIR", "SITE_DIR", "PLAN_CONFIG")
_OTHER_VARS = ("SMTP_HOST", "SMTP_FROM", "SITE_URL")

# The state classes of the plan. Callers test kind, not the reason text, so a
# change to the log text cannot change what a caller does.
Kind = Literal["on", "disabled", "not_configured", "bad_flag", "incomplete"]
ClaudeMode = Literal["none", "api_key", "subscription", "conflict"]

_MODE_KEY: dict[str, str] = {
    "api_key": CLAUDE_API_KEY,
    "subscription": CLAUDE_OAUTH_TOKEN,
}
_MODE_LABEL: dict[str, str] = {"api_key": "API key", "subscription": "subscription"}


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
        # Only claude sets a reason when on: the mode label.
        if self.on:
            return (
                f"{self.name}: on ({self.reason})"
                if self.reason
                else f"{self.name}: on"
            )
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


def _positive_int(raw: str) -> int | None:
    # isdigit alone accepts "²", which int() rejects.
    if not (raw.isascii() and raw.isdigit()):
        return None
    value = int(raw)
    return value if value >= 1 else None


def _smtp_port() -> int | None:
    raw = _env("SMTP_PORT")
    if not raw:
        return DEFAULT_SMTP_PORT
    port = _positive_int(raw)
    return port if port is not None and port <= 65535 else None


def _hc_url() -> str:
    return _env("HEALTHCHECK_URL").rstrip("/")


def _hc_url_valid() -> bool:
    url = _hc_url()
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 — raises ValueError for a port out of range
    except ValueError:
        return False
    # A query or a fragment, even an empty one, would swallow the /fail suffix.
    # urllib rejects a space, a control character or a non-ASCII character only
    # at ping time, where the failure has no cause in the log. A bad paste must
    # fail here. A real ping URL is printable ASCII. urllib also connects to the
    # whole netloc, so userinfo or a backslash gives a host that urlsplit does
    # not see.
    return (
        parts.scheme in ("http", "https")
        and bool(parts.hostname)
        and parts.port != 0
        and "?" not in url
        and "#" not in url
        and all("!" <= c <= "~" for c in url)
        and "@" not in parts.netloc
        and "\\" not in url
    )


def _invalid_values(name: str) -> list[str]:
    """Variables of a feature that are set but have a value that is not valid."""
    problems = []
    if name == "digest":
        if _smtp_port() is None:
            problems.append("SMTP_PORT")
        problems += [v for v in ("SMTP_HOST", "SMTP_FROM") if _unresolved(_env(v))]
    elif name == "healthcheck":
        url = _env("HEALTHCHECK_URL")
        # An op:// value is already a problem, as an unresolved setting.
        if url and not _unresolved(url) and not _hc_url_valid():
            problems.append("HEALTHCHECK_URL")
    return problems


def _present(var: str) -> bool:
    if var == "DIGEST_RECIPIENTS":
        return bool(_recipients())
    return bool(_env(var))


def _key_set(environ: Mapping[str, str], var: str) -> bool:
    value = environ.get(var, "").strip()
    return bool(value) and not _unresolved(value)


def claude_mode(environ: Mapping[str, str] | None = None) -> ClaudeMode:
    """Return the Claude auth mode. An empty or op:// key counts as not set.

    environ defaults to os.environ, read at call time.
    """
    env = os.environ if environ is None else environ
    api_key = _key_set(env, CLAUDE_API_KEY)
    token = _key_set(env, CLAUDE_OAUTH_TOKEN)
    if api_key and token:
        return "conflict"
    if api_key:
        return "api_key"
    if token:
        return "subscription"
    return "none"


def claude_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of environ for the claude subprocess.

    The copy holds no Claude credential except the key of the chosen mode. With
    mode none or conflict, it holds none of them.
    """
    env = {k: v for k, v in environ.items() if k not in CLAUDE_CREDENTIALS}
    key = _MODE_KEY.get(claude_mode(environ))
    if key:
        env[key] = environ[key].strip()
    return env


def _claude_state() -> FeatureState:
    mode = claude_mode()
    if mode == "conflict":
        return FeatureState(
            "claude",
            "incomplete",
            f"both {CLAUDE_API_KEY} and {CLAUDE_OAUTH_TOKEN} are set. "
            "Set one of them, or neither",
        )
    if mode == "none":
        return FeatureState("claude", "not_configured", "not configured")
    return FeatureState("claude", "on", _MODE_LABEL[mode])


def _flag_state(name: str, flag: str) -> FeatureState:
    """Apply the flag rule of a feature that has no settings."""
    flag_value = _env(flag).lower()
    if flag in os.environ and flag_value not in _TRUE | _FALSE:
        # An unknown value fails safe: the feature stays off.
        return FeatureState(name, "bad_flag", f"not valid: {flag}", [flag])
    if flag_value in _FALSE:
        return FeatureState(name, "disabled", f"disabled by {flag}")
    if flag_value in _TRUE:
        return FeatureState(name, "on")
    return FeatureState(name, "not_configured", f"set {flag}=true to turn on")


def _turn_on_rule(name: str) -> str:
    needed = FEATURES[name][1]
    # claude needs exactly one of its keys. The others need all their settings.
    if name == "claude":
        return f"The claude feature is on when exactly one of {' and '.join(needed)} is set."
    *rest, last = needed
    if not rest:
        return f"The {name} feature is on when {last} is set."
    return f"The {name} feature is on when {', '.join(rest)} and {last} are set."


def feature(name: str) -> FeatureState:
    """Apply the on/off rule to one feature."""
    if name not in FEATURES:
        raise ValueError(f"unknown feature: {name}")
    if name == "claude":
        return _claude_state()
    flag, needed = FEATURES[name]
    if flag is not None:
        return _flag_state(name, flag)

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
    """Return HEALTHCHECK_URL (plus /fail), or None when the feature is off."""
    if not feature("healthcheck").on:
        return None
    url = _hc_url()
    return url + "/fail" if status == "fail" else url


def community_notes_url() -> str:
    return _env("CT_COMMUNITY_NOTES_RSS")


def site_url() -> str:
    return _env("SITE_URL")


def _health_max_age() -> int | None:
    raw = _env("HEALTH_MAX_AGE_MINUTES")
    if not raw:
        return DEFAULT_HEALTH_MAX_AGE_MINUTES
    return _positive_int(raw)


def health_max_age_minutes() -> int:
    """Return the maximum age of plan.json for a healthy container.

    A value that is not valid gives the default. check_lines() warns about it.
    """
    return _health_max_age() or DEFAULT_HEALTH_MAX_AGE_MINUTES


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
        elif state.kind == "incomplete":
            # Only the claude conflict (both keys set) has no problem variable to name.
            warnings.append(f"WARNING: {n} is off ({state.reason}).")
    for var in (*_OTHER_VARS, CLAUDE_API_KEY, CLAUDE_OAUTH_TOKEN):
        if _unresolved(_env(var)) and not any(var in s.problems for s in states):
            warnings.append(f"WARNING: {var} is not resolved. It is not used.")
            warnings.append(OP_WARNING)
    if _health_max_age() is None:
        warnings.append(
            "WARNING: HEALTH_MAX_AGE_MINUTES is not a positive integer. "
            f"The health check uses {DEFAULT_HEALTH_MAX_AGE_MINUTES}."
        )
        if _unresolved(_env("HEALTH_MAX_AGE_MINUTES")):
            warnings.append(OP_WARNING)
    if _env(UNUSED_ANTHROPIC_VAR):
        warnings.append(
            f"WARNING: {UNUSED_ANTHROPIC_VAR} is set. It is not used. "
            f"Remove it from .env. Set {CLAUDE_API_KEY} or {CLAUDE_OAUTH_TOKEN}."
        )
    if _env(REMOVED_HA_VAR):
        warnings.append(
            f"WARNING: {REMOVED_HA_VAR} is set. The SSH push was removed. "
            "Remove it from .env. To feed the Home Assistant sensors, set "
            "HA_ENABLED=true and change the sensors to read the site URLs."
        )
    for var, name in REMOVED_FLAGS.items():
        # An empty value counts: before the removal, it turned the feature off.
        if var in os.environ:
            warnings.append(
                f"WARNING: {var} is set. The flag was removed. "
                f"Remove it from .env. {_turn_on_rule(name)}"
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
