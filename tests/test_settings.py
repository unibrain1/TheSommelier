"""Tests for settings.py — the on/off rule, required variables, paths, log."""

import dataclasses
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import settings

REPO = Path(__file__).resolve().parent.parent
SETTINGS_CLI = str(REPO / "scripts" / "settings.py")

# Every variable that settings.py reads. A developer shell can set any of them.
SETTING_VARS = settings.ALL_VARS

# feature -> (flag or None, {variable: value}) with every setting present
FEATURES = {
    "digest": (
        None,
        {
            "SMTP_USERNAME": "SECRET-canary-smtp-user",
            "SMTP_PASSWORD": "SECRET-canary-smtp-pass",
            "DIGEST_RECIPIENTS": "SECRET-canary-rcpt@example.com",
        },
    ),
    "community_notes": (
        None,
        {"CT_COMMUNITY_NOTES_RSS": "https://example.com/SECRET-canary-rss"},
    ),
    "healthcheck": (
        "HEALTHCHECK_ENABLED",
        {"HEALTHCHECK_PING_KEY": "SECRET-canary-hckey"},
    ),
}


# Features in FEATURES that also have an on/off flag.
FLAGGED = [name for name, (flag, _) in FEATURES.items() if flag]

# Features with no settings to check. Only the flag can turn them on.
OPT_IN = {"home_assistant": "HA_ENABLED"}

# The claude feature has no flag. Its keys choose a mode.
API_KEY = "SECRET-canary-api-key"
OAUTH = "SECRET-canary-oauth"
CLAUDE_VALUES = {
    "api_key": {"ANTHROPIC_API_KEY": API_KEY},
    "subscription": {"CLAUDE_CODE_OAUTH_TOKEN": OAUTH},
}
CLAUDE_LOG = {
    "api_key": "claude: on (API key)",
    "subscription": "claude: on (subscription)",
}
CREDENTIALS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")


def test_local_feature_list_matches_settings():
    """A feature added to settings.FEATURES needs a case in this file."""
    assert set(FEATURES) | set(OPT_IN) | {"claude"} == set(settings.FEATURES)


def test_local_flags_match_settings():
    for name, (flag, _) in FEATURES.items():
        assert settings.FEATURES[name][0] == flag
    assert set(settings.REMOVED_FLAGS) == {
        "CLAUDE_ENABLED",
        "DIGEST_ENABLED",
        "COMMUNITY_NOTES_ENABLED",
    }


REQUIRED_VALUES = {
    "USERNAME": "SECRET-canary-user",
    "PASSWORD": "SECRET-canary-password",
    "GOOGLE_CALENDAR_ICS_URL": "https://example.com/SECRET-canary-ics",
}

# Features with more than one setting can be partly set.
MULTI = [(f, v) for f, (_, vs) in FEATURES.items() if len(vs) > 1 for v in vs]


@pytest.fixture
def clean_env(monkeypatch):
    for name in SETTING_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def set_feature(monkeypatch, name, skip=()):
    for var, value in FEATURES[name][1].items():
        if var not in skip:
            monkeypatch.setenv(var, value)


def run_cli(args, values=None):
    """Run settings.py as a script with an explicit environment."""
    env = {"PATH": os.environ["PATH"], **(values or {})}
    return subprocess.run(
        [sys.executable, SETTINGS_CLI, *args],
        env=env,
        capture_output=True,
        text=True,
        cwd=REPO,
    )


def all_canaries():
    values = [*REQUIRED_VALUES.values(), API_KEY, OAUTH]
    for _, vs in FEATURES.values():
        values += list(vs.values())
    return values


def feature_values(name):
    return dict(FEATURES[name][1])


# --- on/off rule for each feature ---------------------------------------


@pytest.mark.parametrize("name", FEATURES)
class TestFeatureRule:
    def test_on_when_all_settings_present(self, clean_env, name):
        set_feature(clean_env, name)
        state = settings.feature(name)
        assert state.on is True
        assert state.problems == []
        assert state.log_line() == f"{name}: on"

    def test_off_not_configured_when_nothing_set(self, clean_env, name):
        state = settings.feature(name)
        assert state.on is False
        assert not state.problems
        assert state.log_line() == f"{name}: off (not configured)"


@pytest.mark.parametrize("name", FLAGGED)
class TestFlagRule:
    @pytest.mark.parametrize("flag_value", ["false", "False", "FALSE", "0", "no", "NO"])
    def test_off_when_flag_false(self, clean_env, name, flag_value):
        flag = FEATURES[name][0]
        set_feature(clean_env, name)
        clean_env.setenv(flag, flag_value)
        state = settings.feature(name)
        assert state.on is False
        assert not state.problems
        assert state.log_line() == f"{name}: off (disabled by {flag})"

    def test_flag_false_wins_when_nothing_else_set(self, clean_env, name):
        flag = FEATURES[name][0]
        clean_env.setenv(flag, "false")
        assert settings.feature(name).log_line() == f"{name}: off (disabled by {flag})"

    @pytest.mark.parametrize("flag_value", ["true", "True", "TRUE", "1", "yes", "YES"])
    def test_on_when_present_and_flag_true(self, clean_env, name, flag_value):
        flag = FEATURES[name][0]
        set_feature(clean_env, name)
        clean_env.setenv(flag, flag_value)
        assert settings.feature(name).on is True

    def test_flag_true_without_settings_stays_off(self, clean_env, name):
        flag = FEATURES[name][0]
        clean_env.setenv(flag, "true")
        assert settings.feature(name).on is False

    @pytest.mark.parametrize("flag_value", ["maybe", "2", "", "   ", "tru"])
    def test_unknown_flag_value_gives_off_with_warning(
        self, clean_env, name, flag_value
    ):
        flag = FEATURES[name][0]
        set_feature(clean_env, name)
        clean_env.setenv(flag, flag_value)
        state = settings.feature(name)
        assert state.on is False
        assert state.problems
        assert state.problems == [flag]
        result = run_cli(
            ["check"], {**REQUIRED_VALUES, **feature_values(name), flag: flag_value}
        )
        assert result.returncode == 0
        assert re.search(rf"WARNING: {name} is off.*{flag}", result.stderr)

    @pytest.mark.parametrize("flag_value", ["maybe", "2", ""])
    def test_enabled_exit_code_one_for_unknown_flag(self, clean_env, name, flag_value):
        flag = FEATURES[name][0]
        result = run_cli(["enabled", name], {**feature_values(name), flag: flag_value})
        assert result.returncode == 1


@pytest.mark.parametrize(("name", "missing"), MULTI)
def test_partial_settings_give_off_with_warning_naming_variables(
    clean_env, name, missing
):
    set_feature(clean_env, name, skip=(missing,))
    state = settings.feature(name)
    assert state.on is False
    assert state.problems == [missing]
    values = {**REQUIRED_VALUES, **feature_values(name)}
    del values[missing]
    result = run_cli(["check"], values)
    assert result.returncode == 0
    assert f"{name}: off (missing or not valid: {missing})" in result.stdout
    assert re.search(rf"WARNING: {name} is off.*{missing}", result.stderr)
    for canary in all_canaries():
        assert canary not in result.stdout
        assert canary not in result.stderr


@pytest.mark.parametrize(
    ("name", "var"),
    [(f, v) for f, (_, vs) in FEATURES.items() for v in vs],
)
def test_op_reference_gives_off_with_warning(clean_env, name, var):
    set_feature(clean_env, name)
    clean_env.setenv(var, "op://vault/item/field-canary")
    state = settings.feature(name)
    assert state.on is False
    assert var in state.problems
    values = {
        **REQUIRED_VALUES,
        **feature_values(name),
        var: "op://vault/item/field-canary",
    }
    result = run_cli(["check"], values)
    assert result.returncode == 0
    assert re.search(rf"WARNING: {name} is off.*{var}", result.stderr)
    assert "op run" in result.stderr
    assert "op://vault/item/field-canary" not in result.stdout + result.stderr


def test_only_op_reference_set_is_not_silently_not_configured(clean_env):
    clean_env.setenv("HEALTHCHECK_PING_KEY", "op://vault/item/key")
    state = settings.feature("healthcheck")
    assert state.on is False
    assert state.problems


def test_unknown_feature_name_raises(clean_env):
    with pytest.raises(ValueError):
        settings.feature("nope")


# --- required variables -------------------------------------------------


@pytest.mark.parametrize("missing", list(REQUIRED_VALUES))
def test_required_missing_exits_nonzero(missing):
    values = {k: v for k, v in REQUIRED_VALUES.items() if k != missing}
    result = run_cli(["check"], values)
    assert result.returncode == 1
    assert missing in result.stderr
    others = [k for k in REQUIRED_VALUES if k != missing]
    for other in others:
        assert f"Required variable {other} " not in result.stderr


def test_required_all_present_exits_zero():
    result = run_cli(["check"], REQUIRED_VALUES)
    assert result.returncode == 0


def test_required_all_missing_names_all_three():
    result = run_cli(["check"], {})
    assert result.returncode == 1
    for name in REQUIRED_VALUES:
        assert name in result.stderr


@pytest.mark.parametrize("empty", list(REQUIRED_VALUES))
@pytest.mark.parametrize("blank", ["", "   "])
def test_required_empty_value_is_missing(empty, blank):
    values = {**REQUIRED_VALUES, empty: blank}
    result = run_cli(["check"], values)
    assert result.returncode == 1
    assert empty in result.stderr


@pytest.mark.parametrize("name", list(REQUIRED_VALUES))
def test_required_op_reference_is_error_without_value(name):
    values = {**REQUIRED_VALUES, name: "op://vault/item/secret-canary"}
    result = run_cli(["check"], values)
    assert result.returncode == 1
    assert name in result.stderr
    assert "secret-canary" not in result.stdout + result.stderr


def test_required_function_lists_missing_names(clean_env):
    clean_env.setenv("USERNAME", "x")
    assert settings.required() == ["PASSWORD", "GOOGLE_CALENDAR_ICS_URL"]


# --- SMTP ---------------------------------------------------------------


def test_smtp_defaults(clean_env):
    set_feature(clean_env, "digest")
    smtp = settings.smtp()
    assert smtp.host == "smtp.gmail.com"
    assert smtp.port == 587


def test_smtp_host_port_from_env(clean_env):
    set_feature(clean_env, "digest")
    clean_env.setenv("SMTP_HOST", "mail.example.test")
    clean_env.setenv("SMTP_PORT", "2525")
    smtp = settings.smtp()
    assert smtp.host == "mail.example.test"
    assert smtp.port == 2525
    assert isinstance(smtp.port, int)
    assert settings.feature("digest").on is True


def test_smtp_from_defaults_to_username(clean_env):
    clean_env.setenv("SMTP_USERNAME", "sender@example.com")
    assert settings.smtp().sender == "sender@example.com"


def test_smtp_from_env_wins(clean_env):
    clean_env.setenv("SMTP_USERNAME", "sender@example.com")
    clean_env.setenv("SMTP_FROM", "Sommelier <wine@example.com>")
    assert settings.smtp().sender == "Sommelier <wine@example.com>"


def test_smtp_recipients_are_split_and_trimmed(clean_env):
    clean_env.setenv("DIGEST_RECIPIENTS", " a@x.com ,b@x.com,, ")
    assert settings.smtp().recipients == ["a@x.com", "b@x.com"]


@pytest.mark.parametrize("port", ["abc", "0", "70000", "-1", "587.5", "65536", "²"])
def test_invalid_smtp_port_turns_digest_off_with_warning(clean_env, port):
    set_feature(clean_env, "digest")
    clean_env.setenv("SMTP_PORT", port)
    state = settings.feature("digest")
    assert state.on is False
    assert "SMTP_PORT" in state.problems
    values = {**REQUIRED_VALUES, **feature_values("digest"), "SMTP_PORT": port}
    result = run_cli(["check"], values)
    assert result.returncode == 0
    assert re.search(r"WARNING: digest is off.*SMTP_PORT", result.stderr)


@pytest.mark.parametrize("port", ["1", "587", "65535"])
def test_valid_smtp_port_boundaries_keep_digest_on(clean_env, port):
    set_feature(clean_env, "digest")
    clean_env.setenv("SMTP_PORT", port)
    assert settings.feature("digest").on is True
    assert settings.smtp().port == int(port)


# --- HEALTH_MAX_AGE_MINUTES ----------------------------------------------


def test_health_max_age_default_is_600(clean_env):
    assert settings.health_max_age_minutes() == 600


def test_health_max_age_from_env(clean_env):
    clean_env.setenv("HEALTH_MAX_AGE_MINUTES", "30")
    assert settings.health_max_age_minutes() == 30


def test_check_gives_no_health_max_age_warning_for_valid_value():
    result = run_cli(["check"], {**REQUIRED_VALUES, "HEALTH_MAX_AGE_MINUTES": "30"})
    assert "HEALTH_MAX_AGE_MINUTES" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "value", ["0", "-5", "abc", "²", "op://vault/SECRET-canary-health/age"]
)
def test_invalid_health_max_age_gives_default_with_warning(clean_env, value):
    clean_env.setenv("HEALTH_MAX_AGE_MINUTES", value)
    assert settings.health_max_age_minutes() == 600
    result = run_cli(["check"], {**REQUIRED_VALUES, "HEALTH_MAX_AGE_MINUTES": value})
    assert result.returncode == 0
    assert "WARNING: HEALTH_MAX_AGE_MINUTES is not a positive integer" in result.stderr
    # A token match, because "0" is part of the default 600 in the warning.
    leak = re.compile(rf"(?<![\w-]){re.escape(value)}(?!\w)")
    assert not leak.search(result.stdout + result.stderr)


@pytest.mark.parametrize("recipients", [" , ,", ",", "   "])
def test_recipients_with_only_separators_count_as_missing(clean_env, recipients):
    set_feature(clean_env, "digest")
    clean_env.setenv("DIGEST_RECIPIENTS", recipients)
    state = settings.feature("digest")
    assert state.on is False
    assert state.problems == ["DIGEST_RECIPIENTS"]


# --- Healthchecks -------------------------------------------------------


def test_healthcheck_defaults_and_url_build(clean_env):
    clean_env.setenv("HEALTHCHECK_PING_KEY", "KEY1")
    assert settings.healthcheck_url() == "https://hc-ping.com/KEY1/the-sommelier"


def test_healthcheck_custom_base_and_slug(clean_env):
    clean_env.setenv("HEALTHCHECK_PING_KEY", "KEY1")
    clean_env.setenv("HEALTHCHECK_BASE_URL", "http://hc.example.test:8000")
    clean_env.setenv("HEALTHCHECK_SLUG", "my-slug")
    assert settings.healthcheck_url() == "http://hc.example.test:8000/KEY1/my-slug"


@pytest.mark.parametrize(
    "base", ["https://hc.example.test/", "https://hc.example.test///"]
)
def test_healthcheck_trailing_slash_gives_no_double_slash(clean_env, base):
    clean_env.setenv("HEALTHCHECK_PING_KEY", "KEY1")
    clean_env.setenv("HEALTHCHECK_BASE_URL", base)
    url = settings.healthcheck_url()
    assert url == "https://hc.example.test/KEY1/the-sommelier"
    assert "//" not in url.split("://", 1)[1]


@pytest.mark.parametrize("base", ["ftp://x", "hc-ping.com", "javascript:x", "http://"])
def test_healthcheck_invalid_base_turns_feature_off(clean_env, base):
    clean_env.setenv("HEALTHCHECK_PING_KEY", "SECRET-canary-hckey")
    clean_env.setenv("HEALTHCHECK_BASE_URL", base)
    state = settings.feature("healthcheck")
    assert state.on is False
    assert state.problems == ["HEALTHCHECK_BASE_URL"]
    assert settings.healthcheck_url() is None


@pytest.mark.parametrize("slug", ["a/b", "a b", "a\tb", "/x"])
def test_healthcheck_invalid_slug_turns_feature_off(clean_env, slug):
    clean_env.setenv("HEALTHCHECK_PING_KEY", "SECRET-canary-hckey")
    clean_env.setenv("HEALTHCHECK_SLUG", slug)
    state = settings.feature("healthcheck")
    assert state.on is False
    assert state.problems == ["HEALTHCHECK_SLUG"]
    assert settings.healthcheck_url() is None


@pytest.mark.parametrize(
    "base",
    [
        "http://[bad",
        "https://h:99999",
        "https://h:abc",
        "https://h/?q=",
        "https://h/#x",
        "https://h:0",
        "https://h/path?",
        "https://h/path#",
    ],
)
def test_healthcheck_base_url_rejected_cases_turn_feature_off(clean_env, base):
    clean_env.setenv("HEALTHCHECK_PING_KEY", "SECRET-canary-hckey")
    clean_env.setenv("HEALTHCHECK_BASE_URL", base)
    state = settings.feature("healthcheck")
    assert state.on is False
    assert state.problems == ["HEALTHCHECK_BASE_URL"]
    assert "HEALTHCHECK_BASE_URL" in state.reason
    assert settings.healthcheck_url() is None
    assert settings.healthcheck_url("fail") is None


def test_healthcheck_base_url_with_valid_port_and_path_is_kept(clean_env):
    clean_env.setenv("HEALTHCHECK_PING_KEY", "KEY1")
    clean_env.setenv("HEALTHCHECK_BASE_URL", "https://h.example.test:65535/ping")
    assert (
        settings.healthcheck_url("fail")
        == "https://h.example.test:65535/ping/KEY1/the-sommelier/fail"
    )


@pytest.mark.parametrize("slug", ["a?b", "a#b", "a.b", "..", ".", "a%2Fb", "é"])
def test_healthcheck_slug_outside_allowed_set_turns_feature_off(clean_env, slug):
    clean_env.setenv("HEALTHCHECK_PING_KEY", "KEY1")
    clean_env.setenv("HEALTHCHECK_SLUG", slug)
    state = settings.feature("healthcheck")
    assert state.on is False
    assert state.problems == ["HEALTHCHECK_SLUG"]
    assert settings.healthcheck_url() is None


@pytest.mark.parametrize("slug", ["abc", "A_b-9", "0"])
def test_healthcheck_slug_in_allowed_set_is_used(clean_env, slug):
    clean_env.setenv("HEALTHCHECK_PING_KEY", "KEY1")
    clean_env.setenv("HEALTHCHECK_SLUG", slug)
    assert settings.healthcheck_url() == f"https://hc-ping.com/KEY1/{slug}"


@pytest.mark.parametrize("key", ["a/b", "a?b", "a b", "a#b", "a.b", "../x"])
def test_healthcheck_key_outside_allowed_set_turns_feature_off(clean_env, key):
    clean_env.setenv("HEALTHCHECK_PING_KEY", key)
    state = settings.feature("healthcheck")
    assert state.on is False
    assert state.problems == ["HEALTHCHECK_PING_KEY"]
    assert settings.healthcheck_url() is None
    # The warning names the variable and never holds the value.
    _, warnings, _ = settings.check_lines()
    assert any("HEALTHCHECK_PING_KEY" in w for w in warnings)
    assert not any(key in w for w in warnings)


def test_healthcheck_op_key_is_reported_as_unresolved_not_as_invalid_chars(clean_env):
    clean_env.setenv("HEALTHCHECK_PING_KEY", "op://vault/item/key")
    state = settings.feature("healthcheck")
    assert state.on is False
    assert state.problems == ["HEALTHCHECK_PING_KEY"]
    _, warnings, _ = settings.check_lines()
    assert any("op://" in w for w in warnings)
    assert not any("vault/item" in w for w in warnings)


def test_feature_state_is_frozen(clean_env):
    state = settings.feature("claude")
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.on = True  # type: ignore[misc]


def test_smtp_settings_is_frozen(clean_env):
    for name, value in FEATURES["digest"][1].items():
        clean_env.setenv(name, value)
    smtp = settings.smtp()
    with pytest.raises(dataclasses.FrozenInstanceError):
        smtp.host = "evil.example.test"  # type: ignore[misc]


def test_smtp_password_is_not_in_repr(clean_env):
    for name, value in FEATURES["digest"][1].items():
        clean_env.setenv(name, value)
    smtp = settings.smtp()
    assert smtp.password == "SECRET-canary-smtp-pass"
    assert "SECRET-canary-smtp-pass" not in repr(smtp)
    assert "SECRET-canary-smtp-pass" not in str(smtp)
    # The other fields stay visible for debugging.
    assert "SECRET-canary-smtp-user" in repr(smtp)


def test_healthcheck_url_none_when_disabled(clean_env):
    clean_env.setenv("HEALTHCHECK_PING_KEY", "KEY1")
    clean_env.setenv("HEALTHCHECK_ENABLED", "false")
    assert settings.healthcheck_url() is None


# --- startup log --------------------------------------------------------


def test_check_log_lists_every_feature_and_no_secret():
    values = dict(REQUIRED_VALUES)
    for name in FEATURES:
        values.update(feature_values(name))
    values.update(CLAUDE_VALUES["subscription"])
    values["ANTHROPIC_AUTH_TOKEN"] = "SECRET-canary-anthropic-token"
    result = run_cli(["check"], values)
    assert result.returncode == 0
    for name in FEATURES:
        assert re.search(rf"^\s*{name}: on$", result.stdout, re.MULTILINE)
    assert re.search(r"^\s*claude: on \(subscription\)$", result.stdout, re.MULTILINE)
    combined = result.stdout + result.stderr
    for canary in all_canaries():
        assert canary not in combined
    assert "SECRET-canary" not in combined


def test_check_log_shows_state_of_off_features():
    values = {
        **REQUIRED_VALUES,
        "SMTP_USERNAME": "SECRET-canary-smtp-user",
    }
    result = run_cli(["check"], values)
    assert result.returncode == 0
    assert "claude: off (not configured)" in result.stdout
    assert "healthcheck: off (not configured)" in result.stdout
    assert "community_notes: off (not configured)" in result.stdout
    assert re.search(
        r"digest: off \(missing or not valid: SMTP_PASSWORD, DIGEST_RECIPIENTS\)",
        result.stdout,
    )
    assert "SECRET-canary" not in result.stdout + result.stderr


def test_check_gives_no_warning_when_all_features_not_configured():
    result = run_cli(["check"], REQUIRED_VALUES)
    assert result.returncode == 0
    assert "WARNING" not in result.stdout + result.stderr


@pytest.mark.parametrize("extra", [{}, CLAUDE_VALUES["subscription"]])
def test_check_warns_auth_token_is_not_used(extra):
    values = {**REQUIRED_VALUES, **extra, "ANTHROPIC_AUTH_TOKEN": "SECRET-canary-gw"}
    result = run_cli(["check"], values)
    assert result.returncode == 0
    lines = [ln for ln in result.stderr.splitlines() if "ANTHROPIC_AUTH_TOKEN" in ln]
    assert len(lines) == 1
    assert lines[0].startswith("WARNING: ANTHROPIC_AUTH_TOKEN is set. It is not used.")
    assert "SECRET-canary" not in result.stdout + result.stderr


def test_auth_token_alone_is_not_a_mode(clean_env):
    clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "SECRET-canary-gw")
    assert settings.claude_mode() == "none"
    assert settings.feature("claude").kind == "not_configured"


def test_check_gives_no_warning_for_api_key_alone():
    result = run_cli(["check"], {**REQUIRED_VALUES, **CLAUDE_VALUES["api_key"]})
    assert result.returncode == 0
    assert "claude: on (API key)" in result.stdout
    assert "WARNING" not in result.stderr
    assert API_KEY not in result.stdout + result.stderr


def test_check_no_anthropic_warning_when_unset():
    result = run_cli(["check"], REQUIRED_VALUES)
    assert "ANTHROPIC" not in result.stdout + result.stderr


# --- paths and call-time reads ------------------------------------------


def test_values_read_at_call_time(clean_env, tmp_path):
    clean_env.setenv("DATA_DIR", str(tmp_path / "a"))
    assert settings.data_dir() == tmp_path / "a"
    clean_env.setenv("DATA_DIR", str(tmp_path / "b"))
    assert settings.data_dir() == tmp_path / "b"
    assert settings.feature("claude").on is False
    clean_env.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    assert settings.feature("claude").on is True
    clean_env.setenv("ANTHROPIC_API_KEY", "k")
    assert settings.feature("claude").on is False


def test_data_dir_default_is_slash_data(clean_env):
    assert settings.data_dir() == Path("/data")


def test_plan_config_default_is_config_plan_yaml(clean_env):
    assert settings.plan_config_path() == Path("/config/plan.yaml")


def test_plan_config_from_env(clean_env, tmp_path):
    clean_env.setenv("PLAN_CONFIG", str(tmp_path / "plan.yaml"))
    assert settings.plan_config_path() == tmp_path / "plan.yaml"


def test_plan_config_empty_is_default(clean_env):
    clean_env.setenv("PLAN_CONFIG", "")
    assert settings.plan_config_path() == Path("/config/plan.yaml")


def test_data_dir_empty_is_default(clean_env):
    clean_env.setenv("DATA_DIR", "")
    assert settings.data_dir() == Path("/data")


def test_data_dir_relative_value_is_accepted(clean_env):
    clean_env.setenv("DATA_DIR", "rel/data")
    assert settings.data_dir() == Path("rel/data")


def test_site_dir_default_is_data_dir_site(clean_env):
    assert settings.site_dir() == Path("/data/site")


def test_site_dir_follows_data_dir(clean_env, tmp_path):
    clean_env.setenv("DATA_DIR", str(tmp_path))
    assert settings.site_dir() == tmp_path / "site"


def test_explicit_site_dir_wins(clean_env, tmp_path):
    clean_env.setenv("DATA_DIR", str(tmp_path / "d"))
    clean_env.setenv("SITE_DIR", str(tmp_path / "elsewhere"))
    assert settings.site_dir() == tmp_path / "elsewhere"


def test_get_data_dir_default_is_data():
    result = run_cli(["get", "data_dir"])
    assert result.returncode == 0
    assert result.stdout.strip() == "/data"


def test_get_data_dir_empty_is_default():
    result = run_cli(["get", "data_dir"], {"DATA_DIR": ""})
    assert result.returncode == 0
    assert result.stdout.strip() == "/data"


def test_get_site_dir_default_is_data_dir_site():
    result = run_cli(["get", "site_dir"])
    assert result.returncode == 0
    assert result.stdout.strip() == "/data/site"


def test_get_site_dir_follows_data_dir(tmp_path):
    result = run_cli(["get", "site_dir"], {"DATA_DIR": str(tmp_path)})
    assert result.returncode == 0
    assert result.stdout.strip() == str(tmp_path / "site")


def test_get_explicit_site_dir_wins(tmp_path):
    result = run_cli(
        ["get", "site_dir"],
        {"DATA_DIR": str(tmp_path / "d"), "SITE_DIR": str(tmp_path / "s")},
    )
    assert result.returncode == 0
    assert result.stdout.strip() == str(tmp_path / "s")


def test_get_unknown_name_exits_2():
    result = run_cli(["get", "password"], {"PASSWORD": "SECRET-canary-password"})
    assert result.returncode == 2
    assert "SECRET-canary" not in result.stdout + result.stderr


def test_no_arguments_exits_2_with_usage():
    result = run_cli([])
    assert result.returncode == 2
    assert "Usage" in result.stderr


@pytest.mark.parametrize("name", FEATURES)
def test_enabled_exit_codes(name):
    on = run_cli(["enabled", name], feature_values(name))
    assert on.returncode == 0
    off = run_cli(["enabled", name], {})
    assert off.returncode == 1
    results = [on, off]
    flag = FEATURES[name][0]
    if flag:
        disabled = run_cli(["enabled", name], {**feature_values(name), flag: "false"})
        assert disabled.returncode == 1
        results.append(disabled)
    for result in results:
        assert "SECRET-canary" not in result.stdout + result.stderr


# --- kind, op:// in lists and in optional values ---------------------------


@pytest.mark.parametrize(
    "flag_value, kind",
    [("false", "disabled"), ("maybe", "bad_flag"), (None, "on")],
)
def test_kind_names_the_state_class(clean_env, flag_value, kind):
    set_feature(clean_env, "healthcheck")
    if flag_value is not None:
        clean_env.setenv("HEALTHCHECK_ENABLED", flag_value)
    assert settings.feature("healthcheck").kind == kind


def test_kind_not_configured_and_incomplete(clean_env):
    assert settings.feature("digest").kind == "not_configured"
    set_feature(clean_env, "digest", skip=("SMTP_PASSWORD",))
    assert settings.feature("digest").kind == "incomplete"


def test_op_reference_in_recipient_list_turns_digest_off(clean_env):
    set_feature(clean_env, "digest")
    clean_env.setenv("DIGEST_RECIPIENTS", "a@example.com,op://vault/item/rcpt")
    state = settings.feature("digest")
    assert state.on is False
    assert state.problems == ["DIGEST_RECIPIENTS"]
    _, warnings, _ = settings.check_lines()
    assert settings.OP_WARNING in warnings


@pytest.mark.parametrize("var", ["SMTP_HOST", "SMTP_FROM"])
def test_op_reference_in_smtp_option_turns_digest_off(clean_env, var):
    set_feature(clean_env, "digest")
    clean_env.setenv(var, "op://vault/item/field")
    state = settings.feature("digest")
    assert state.on is False
    assert var in state.problems


@pytest.mark.parametrize("var", ["DATA_DIR", "SITE_DIR", "PLAN_CONFIG"])
def test_op_reference_in_path_is_error(var):
    result = run_cli(["check"], {**REQUIRED_VALUES, var: "op://vault/item/path"})
    assert result.returncode == 1
    assert f"ERROR: {var} is not resolved" in result.stderr
    assert "op://vault" not in result.stdout + result.stderr


def test_op_reference_in_site_url_is_warning(clean_env):
    clean_env.setenv("SITE_URL", "op://vault/item/url")
    _, warnings, errors = settings.check_lines()
    assert "WARNING: SITE_URL is not resolved. It is not used." in warnings
    assert settings.OP_WARNING in warnings
    assert not any("SITE_URL" in e for e in errors)


def test_smtp_port_is_none_when_not_valid(clean_env):
    clean_env.setenv("SMTP_PORT", "abc")
    assert settings.smtp().port is None


# --- opt-in features (no settings besides the flag) ---------------------


@pytest.mark.parametrize(("name", "flag"), OPT_IN.items())
class TestOptInFeature:
    def test_off_when_flag_not_set(self, clean_env, name, flag):
        state = settings.feature(name)
        assert state.kind == "not_configured"
        assert state.on is False
        assert state.log_line() == f"{name}: off (set {flag}=true to turn on)"
        assert settings._main(["enabled", name]) == 1

    @pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes", "YES"])
    def test_on_when_flag_true(self, clean_env, name, flag, value):
        clean_env.setenv(flag, value)
        state = settings.feature(name)
        assert state.on is True
        assert state.log_line() == f"{name}: on"
        assert settings._main(["enabled", name]) == 0

    @pytest.mark.parametrize("value", ["false", "False", "0", "no", "NO"])
    def test_disabled_when_flag_false(self, clean_env, name, flag, value):
        clean_env.setenv(flag, value)
        state = settings.feature(name)
        assert state.kind == "disabled"
        assert settings._main(["enabled", name]) == 1

    @pytest.mark.parametrize("value", ["maybe", "2", "", "   ", "tru"])
    def test_bad_flag_value_keeps_feature_off_with_warning(
        self, clean_env, name, flag, value
    ):
        clean_env.setenv(flag, value)
        state = settings.feature(name)
        assert state.kind == "bad_flag"
        assert state.problems == [flag]
        assert settings._main(["enabled", name]) == 1
        result = run_cli(["check"], {**REQUIRED_VALUES, flag: value})
        assert result.returncode == 0
        assert f"WARNING: {name} is off" in result.stderr
        assert flag in result.stderr

    def test_check_log_shows_state_when_on(self, clean_env, name, flag):
        on = run_cli(["check"], {**REQUIRED_VALUES, flag: "true"})
        assert re.search(rf"^\s*{name}: on$", on.stdout, re.MULTILINE)
        assert "WARNING" not in on.stdout + on.stderr


# --- removed HA_SSH_TARGET ------------------------------------------------


def test_removed_ssh_target_gives_warning_without_value(clean_env):
    result = run_cli(
        ["check"], {**REQUIRED_VALUES, "HA_SSH_TARGET": "root@secret-host"}
    )
    assert result.returncode == 0
    assert "WARNING: HA_SSH_TARGET is set. The SSH push was removed." in result.stderr
    assert "secret-host" not in result.stdout + result.stderr


def test_no_ssh_target_warning_when_not_set(clean_env):
    result = run_cli(["check"], REQUIRED_VALUES)
    assert "HA_SSH_TARGET" not in result.stdout + result.stderr


# --- Claude auth mode ----------------------------------------------------


@pytest.mark.parametrize(
    ("values", "mode"),
    [
        ({}, "none"),
        (CLAUDE_VALUES["api_key"], "api_key"),
        (CLAUDE_VALUES["subscription"], "subscription"),
        ({**CLAUDE_VALUES["api_key"], **CLAUDE_VALUES["subscription"]}, "conflict"),
        # An op:// value is a secret the host did not resolve: not set.
        (
            {"ANTHROPIC_API_KEY": "op://v/i/k", "CLAUDE_CODE_OAUTH_TOKEN": OAUTH},
            "subscription",
        ),
        (
            {"ANTHROPIC_API_KEY": API_KEY, "CLAUDE_CODE_OAUTH_TOKEN": "op://v/i/t"},
            "api_key",
        ),
        ({"CLAUDE_CODE_OAUTH_TOKEN": "op://v/i/t"}, "none"),
        # Compose passes an unset host variable as an empty string.
        ({"ANTHROPIC_API_KEY": "", "CLAUDE_CODE_OAUTH_TOKEN": OAUTH}, "subscription"),
        ({"ANTHROPIC_API_KEY": "   ", "CLAUDE_CODE_OAUTH_TOKEN": ""}, "none"),
        ({"ANTHROPIC_AUTH_TOKEN": "SECRET-canary-gw"}, "none"),
    ],
)
def test_claude_mode(values, mode):
    assert settings.claude_mode(values) == mode


def test_claude_mode_reads_os_environ_by_default(clean_env):
    assert settings.claude_mode() == "none"
    clean_env.setenv("ANTHROPIC_API_KEY", API_KEY)
    assert settings.claude_mode() == "api_key"


@pytest.mark.parametrize("mode", ["api_key", "subscription"])
def test_claude_on_logs_the_mode(clean_env, mode):
    for var, value in CLAUDE_VALUES[mode].items():
        clean_env.setenv(var, value)
    state = settings.feature("claude")
    assert state.kind == "on"
    assert state.log_line() == CLAUDE_LOG[mode]
    result = run_cli(["check"], {**REQUIRED_VALUES, **CLAUDE_VALUES[mode]})
    assert re.search(
        rf"^\s*{re.escape(CLAUDE_LOG[mode])}$", result.stdout, re.MULTILINE
    )
    assert run_cli(["enabled", "claude"], CLAUDE_VALUES[mode]).returncode == 0


def test_claude_off_when_no_key(clean_env):
    state = settings.feature("claude")
    assert state.kind == "not_configured"
    assert state.log_line() == "claude: off (not configured)"
    assert run_cli(["enabled", "claude"], {}).returncode == 1


def test_claude_conflict_is_incomplete_and_names_both_keys(clean_env):
    clean_env.setenv("ANTHROPIC_API_KEY", API_KEY)
    clean_env.setenv("CLAUDE_CODE_OAUTH_TOKEN", OAUTH)
    state = settings.feature("claude")
    assert state.kind == "incomplete"
    assert "ANTHROPIC_API_KEY" in state.reason
    assert "CLAUDE_CODE_OAUTH_TOKEN" in state.reason


def test_check_with_both_keys_warns_once_and_exits_zero():
    values = {
        **REQUIRED_VALUES,
        **CLAUDE_VALUES["api_key"],
        **CLAUDE_VALUES["subscription"],
    }
    result = run_cli(["check"], values)
    assert result.returncode == 0
    assert "ERROR" not in result.stderr
    assert "claude: off" in result.stdout
    warnings = [ln for ln in result.stderr.splitlines() if ln.startswith("WARNING")]
    assert len(warnings) == 1
    assert "ANTHROPIC_API_KEY" in warnings[0]
    assert "CLAUDE_CODE_OAUTH_TOKEN" in warnings[0]
    assert run_cli(["enabled", "claude"], values).returncode == 1
    assert "SECRET-canary" not in result.stdout + result.stderr


@pytest.mark.parametrize("var", ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"])
def test_check_warns_op_reference_in_claude_key(var):
    result = run_cli(["check"], {**REQUIRED_VALUES, var: "op://vault/SECRET-canary/k"})
    assert result.returncode == 0
    assert "claude: off (not configured)" in result.stdout
    assert f"WARNING: {var} is not resolved. It is not used." in result.stderr
    assert "op run" in result.stderr
    assert "SECRET-canary" not in result.stdout + result.stderr


REMOVED_FLAGS = settings.REMOVED_FLAGS


def _removed_flag_values(var):
    name = REMOVED_FLAGS[var]
    values = CLAUDE_VALUES["subscription"] if name == "claude" else feature_values(name)
    return name, values


@pytest.mark.parametrize("var", REMOVED_FLAGS)
@pytest.mark.parametrize("flag_value", ["false", "true", "", "SECRET-canary-flag"])
def test_removed_flag_warns_once_and_has_no_effect(clean_env, var, flag_value):
    name, values = _removed_flag_values(var)
    for key, value in {**values, var: flag_value}.items():
        clean_env.setenv(key, value)
    assert settings.feature(name).on is True
    env = {**REQUIRED_VALUES, **values, var: flag_value}
    result = run_cli(["check"], env)
    assert result.returncode == 0
    assert re.search(rf"^\s*{name}: on\b", result.stdout, re.MULTILINE)
    lines = [ln for ln in result.stderr.splitlines() if var in ln]
    assert len(lines) == 1
    assert lines[0].startswith(
        f"WARNING: {var} is set. The flag was removed. Remove it from .env."
    )
    assert "SECRET-canary" not in result.stdout + result.stderr
    assert run_cli(["enabled", name], env).returncode == 0


@pytest.mark.parametrize(
    ("var", "rule"),
    [
        (
            "CLAUDE_ENABLED",
            "The claude feature is on when exactly one of ANTHROPIC_API_KEY and "
            "CLAUDE_CODE_OAUTH_TOKEN is set.",
        ),
        (
            "DIGEST_ENABLED",
            "The digest feature is on when SMTP_USERNAME, SMTP_PASSWORD and "
            "DIGEST_RECIPIENTS are set.",
        ),
        (
            "COMMUNITY_NOTES_ENABLED",
            "The community_notes feature is on when CT_COMMUNITY_NOTES_RSS is set.",
        ),
    ],
)
def test_removed_flag_warning_names_the_settings(clean_env, var, rule):
    clean_env.setenv(var, "false")
    _, warnings, _ = settings.check_lines()
    assert (
        f"WARNING: {var} is set. The flag was removed. Remove it from .env. {rule}"
        in warnings
    )


@pytest.mark.parametrize("var", REMOVED_FLAGS)
def test_removed_flag_does_not_turn_a_feature_on(clean_env, var):
    name = REMOVED_FLAGS[var]
    clean_env.setenv(var, "true")
    assert settings.feature(name).on is False


@pytest.mark.parametrize("var", REMOVED_FLAGS)
def test_no_removed_flag_warning_when_not_set(var):
    _, values = _removed_flag_values(var)
    result = run_cli(["check"], {**REQUIRED_VALUES, **values})
    assert var not in result.stdout + result.stderr


ALL_CREDENTIALS = {
    "ANTHROPIC_API_KEY": API_KEY,
    "ANTHROPIC_AUTH_TOKEN": "SECRET-canary-gw",
    "CLAUDE_CODE_OAUTH_TOKEN": OAUTH,
}


@pytest.mark.parametrize(
    ("values", "kept"),
    [
        ({}, {}),
        ({"ANTHROPIC_AUTH_TOKEN": "SECRET-canary-gw"}, {}),
        (
            {**CLAUDE_VALUES["api_key"], "ANTHROPIC_AUTH_TOKEN": "g"},
            CLAUDE_VALUES["api_key"],
        ),
        (
            {**CLAUDE_VALUES["subscription"], "ANTHROPIC_AUTH_TOKEN": "g"},
            CLAUDE_VALUES["subscription"],
        ),
        (ALL_CREDENTIALS, {}),
        (
            {"ANTHROPIC_API_KEY": "op://v/i/k", "CLAUDE_CODE_OAUTH_TOKEN": OAUTH},
            CLAUDE_VALUES["subscription"],
        ),
        (
            {"ANTHROPIC_API_KEY": "", "CLAUDE_CODE_OAUTH_TOKEN": OAUTH},
            CLAUDE_VALUES["subscription"],
        ),
        ({"CLAUDE_CODE_OAUTH_TOKEN": f"  {OAUTH}\n"}, CLAUDE_VALUES["subscription"]),
    ],
)
def test_claude_env_holds_only_the_mode_credential(values, kept):
    environ = {"PATH": "/usr/bin", "HOME": "/home/x", **values}
    before = dict(environ)
    env = settings.claude_env(environ)
    assert {k: v for k, v in env.items() if k in CREDENTIALS} == kept
    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/home/x"
    # The caller's mapping stays as it was.
    assert environ == before
