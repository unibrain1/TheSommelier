"""Tests for the Claude CLI call in wine_utils.py."""

import subprocess

import pytest

import settings
import wine_utils


@pytest.fixture
def clean_env(monkeypatch):
    for name in settings.ALL_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _capture_run(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append({"args": args, **kwargs})
        return subprocess.CompletedProcess(args, 0, stdout=" reply \n", stderr="")

    monkeypatch.setattr(wine_utils.subprocess, "run", fake_run)
    return calls


def test_call_claude_passes_only_the_mode_credential(clean_env):
    clean_env.setenv("CLAUDE_CODE_OAUTH_TOKEN", "SECRET-canary-oauth")
    clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "SECRET-canary-gw")
    clean_env.setenv("PASSWORD", "SECRET-canary-password")
    clean_env.setenv("PATH", "/usr/bin:/bin")
    calls = _capture_run(clean_env)

    assert wine_utils.call_claude("prompt") == "reply"

    env = calls[0]["env"]
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "SECRET-canary-oauth"
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "PASSWORD" not in env
    assert env["PATH"] == "/usr/bin:/bin"


def test_call_claude_gives_the_cli_no_tools(clean_env):
    # .claude/rules/security.md: the claude CLI call gets no tools.
    calls = _capture_run(clean_env)

    wine_utils.call_claude("prompt")

    args = calls[0]["args"]
    assert args[args.index("--tools") + 1] == ""
    # --tools takes a list. Without "--", the CLI reads the prompt as a tool
    # name and gets no prompt (checked with the pinned CLI 2.1.114).
    assert args[-2:] == ["--", "prompt"]


def test_call_claude_passes_no_credential_when_both_keys_set(clean_env):
    clean_env.setenv("CLAUDE_CODE_OAUTH_TOKEN", "SECRET-canary-oauth")
    clean_env.setenv("ANTHROPIC_API_KEY", "SECRET-canary-api-key")
    calls = _capture_run(clean_env)

    wine_utils.call_claude("prompt")

    assert not set(settings.CLAUDE_CREDENTIALS) & set(calls[0]["env"])


@pytest.mark.parametrize(
    ("stdout", "stderr", "expected"),
    [
        ("Invalid API key · Fix external API key\n", "", "Invalid API key"),
        ("ignored", "stderr cause\n", "stderr cause"),
        ("x" * 500, "", "x" * 300),
    ],
)
def test_call_claude_failure_logs_the_cause(
    clean_env, capsys, stdout, stderr, expected
):
    def fail(args, **_kwargs):
        return subprocess.CompletedProcess(args, 1, stdout=stdout, stderr=stderr)

    clean_env.setattr(wine_utils.subprocess, "run", fail)

    assert wine_utils.call_claude("prompt") == ""
    err = capsys.readouterr().err
    assert f"WARNING: Claude CLI returned 1: {expected}" in err
    assert "x" * 301 not in err


@pytest.mark.parametrize("name", settings.CLAUDE_CREDENTIALS)
def test_call_claude_failure_masks_credentials(clean_env, capsys, name):
    clean_env.setenv(name, "SECRET-canary-key")

    def fail(args, **_kwargs):
        return subprocess.CompletedProcess(
            args, 1, stdout="bad key SECRET-canary-key\n", stderr=""
        )

    clean_env.setattr(wine_utils.subprocess, "run", fail)

    assert wine_utils.call_claude("prompt") == ""
    err = capsys.readouterr().err
    assert "SECRET-canary-key" not in err
    assert "bad key ***" in err


def test_call_claude_without_cli_gives_empty_result(clean_env, capsys):
    def missing(*_args, **_kwargs):
        raise FileNotFoundError("claude-canary")

    clean_env.setattr(wine_utils.subprocess, "run", missing)

    assert wine_utils.call_claude("prompt") == ""
    err = capsys.readouterr().err
    assert "WARNING: Claude CLI could not start" in err
    assert "claude-canary" in err


def test_extract_json_warns_when_the_reply_has_no_json_object(capsys):
    assert wine_utils.extract_json("Sorry, I cannot help with that.") == {}
    assert "WARNING: Claude response has no JSON object" in capsys.readouterr().err
