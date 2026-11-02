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

# feature -> (flag, {variable: value}) with every setting present
FEATURES = {
    "digest": (
        "DIGEST_ENABLED",
        {
            "SMTP_USERNAME": "SECRET-canary-smtp-user",
            "SMTP_PASSWORD": "SECRET-canary-smtp-pass",
            "DIGEST_RECIPIENTS": "SECRET-canary-rcpt@example.com",
        },
    ),
    "claude": ("CLAUDE_ENABLED", {"CLAUDE_CODE_OAUTH_TOKEN": "SECRET-canary-oauth"}),
    "community_notes": (
        "COMMUNITY_NOTES_ENABLED",
        {"CT_COMMUNITY_NOTES_RSS": "https://example.com/SECRET-canary-rss"},
    ),
    "healthcheck": (
        "HEALTHCHECK_ENABLED",
        {"HEALTHCHECK_PING_KEY": "SECRET-canary-hckey"},
    ),
}


def test_local_feature_list_matches_settings():
    """A feature added to settings.FEATURES needs a case in this file."""
    assert set(FEATURES) == set(settings.FEATURES)


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
    values = list(REQUIRED_VALUES.values())
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
    values["ANTHROPIC_API_KEY"] = "SECRET-canary-anthropic-key"
    values["ANTHROPIC_AUTH_TOKEN"] = "SECRET-canary-anthropic-token"
    result = run_cli(["check"], values)
    assert result.returncode == 0
    for name in FEATURES:
        assert re.search(rf"^\s*{name}: on$", result.stdout, re.MULTILINE)
    combined = result.stdout + result.stderr
    for canary in all_canaries():
        assert canary not in combined
    assert "SECRET-canary" not in combined


def test_check_log_shows_state_of_off_features():
    values = {
        **REQUIRED_VALUES,
        "CLAUDE_ENABLED": "false",
        "SMTP_USERNAME": "SECRET-canary-smtp-user",
    }
    result = run_cli(["check"], values)
    assert result.returncode == 0
    assert "claude: off (disabled by CLAUDE_ENABLED)" in result.stdout
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


@pytest.mark.parametrize("var", ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"])
def test_check_warns_on_anthropic_var(var):
    result = run_cli(["check"], {**REQUIRED_VALUES, var: "SECRET-canary-anthropic"})
    assert result.returncode == 0
    assert f"WARNING: {var} is set" in result.stderr
    assert "CLAUDE_CODE_OAUTH_TOKEN" in result.stderr
    assert "SECRET-canary-anthropic" not in result.stdout + result.stderr


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
    clean_env.setenv("CLAUDE_ENABLED", "false")
    assert settings.feature("claude").on is False


def test_data_dir_default_is_slash_data(clean_env):
    assert settings.data_dir() == Path("/data")


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
    flag = FEATURES[name][0]
    disabled = run_cli(["enabled", name], {**feature_values(name), flag: "false"})
    assert disabled.returncode == 1
    for result in (on, off, disabled):
        assert "SECRET-canary" not in result.stdout + result.stderr


# --- kind, op:// in lists and in optional values ---------------------------


@pytest.mark.parametrize(
    "flag_value, kind",
    [("false", "disabled"), ("maybe", "bad_flag"), (None, "on")],
)
def test_kind_names_the_state_class(clean_env, flag_value, kind):
    set_feature(clean_env, "digest")
    if flag_value is not None:
        clean_env.setenv("DIGEST_ENABLED", flag_value)
    assert settings.feature("digest").kind == kind


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


@pytest.mark.parametrize("var", ["DATA_DIR", "SITE_DIR"])
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
