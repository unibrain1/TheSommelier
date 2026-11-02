"""Tests for healthcheck.py — the ping URL, the retries, and the no-key rule."""

import http.server
import os
import socket
import subprocess
import sys
import threading
import urllib.error
from pathlib import Path

import pytest

import healthcheck
import settings

REPO = Path(__file__).resolve().parent.parent
KEY = "SECRET-canary-hckey"
WARNING = "WARNING: Failed to send Healthchecks.io ping"

SETTING_VARS = settings.ALL_VARS


@pytest.fixture
def clean_env(monkeypatch):
    for name in SETTING_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class PingServer:
    """A local HTTP server that records each request."""

    def __init__(self):
        self.requests: list[dict] = []
        self.statuses: list[int] = []  # one status per request, then 200
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                owner.requests.append(
                    {"method": self.command, "path": self.path, "body": body}
                )
                status = owner.statuses.pop(0) if owner.statuses else 200
                self.send_response(status)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"OK")

            def log_message(self, *args):
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        self.thread = threading.Thread(
            target=self.httpd.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def server(clean_env):
    srv = PingServer()
    clean_env.setattr(healthcheck, "RETRY_DELAY_SECONDS", 0)
    clean_env.setenv("HEALTHCHECK_BASE_URL", srv.base)
    clean_env.setenv("HEALTHCHECK_PING_KEY", KEY)
    yield srv
    srv.close()


def test_success_posts_to_base_key_slug(server):
    message = "Pipeline ok\nBottles: 12 — café ✓"
    rc = healthcheck.main(["success", message])
    assert rc == 0
    assert len(server.requests) == 1
    request = server.requests[0]
    assert request["method"] == "POST"
    assert request["path"] == f"/{KEY}/the-sommelier"
    assert request["body"].decode("utf-8") == message


def test_fail_appends_fail(server):
    rc = healthcheck.main(["fail", "boom"])
    assert rc == 0
    assert [r["path"] for r in server.requests] == [f"/{KEY}/the-sommelier/fail"]
    assert server.requests[0]["body"] == b"boom"


def test_custom_base_and_slug(server, clean_env):
    clean_env.setenv("HEALTHCHECK_BASE_URL", server.base + "/")
    clean_env.setenv("HEALTHCHECK_SLUG", "my-check")
    rc = healthcheck.main(["success", "hi"])
    assert rc == 0
    assert [r["path"] for r in server.requests] == [f"/{KEY}/my-check"]


@pytest.mark.parametrize(
    "case", ["flag_false", "no_key", "op_reference_key", "invalid_slug", "invalid_base"]
)
def test_disabled_makes_no_request(server, clean_env, capsys, case):
    if case == "flag_false":
        clean_env.setenv("HEALTHCHECK_ENABLED", "false")
    elif case == "no_key":
        clean_env.delenv("HEALTHCHECK_PING_KEY")
    elif case == "op_reference_key":
        clean_env.setenv("HEALTHCHECK_PING_KEY", "op://vault/item/key")
    elif case == "invalid_slug":
        clean_env.setenv("HEALTHCHECK_SLUG", "a/b")
    else:
        clean_env.setenv("HEALTHCHECK_BASE_URL", "ftp://127.0.0.1")
    rc = healthcheck.main(["success", "hi"])
    rc_fail = healthcheck.main(["fail", "hi"])
    assert (rc, rc_fail) == (0, 0)
    assert server.requests == []
    assert WARNING not in capsys.readouterr().err


def test_retries_three_times_then_succeeds(server, capsys):
    server.statuses = [500, 500]
    rc = healthcheck.main(["success", "hi"])
    assert rc == 0
    assert len(server.requests) == 3
    assert WARNING not in capsys.readouterr().err


def test_failure_exits_zero_with_warning(server, capsys):
    server.statuses = [500, 500, 500, 500]
    rc = healthcheck.main(["success", "hi"])
    captured = capsys.readouterr()
    assert rc == 0
    assert len(server.requests) == 3
    assert captured.err.strip() == WARNING


def _closed_port_url():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}"


@pytest.mark.parametrize("failure", ["http_error", "timeout", "connection_refused"])
def test_key_never_in_output(server, clean_env, capsys, failure):
    if failure == "http_error":
        server.statuses = [500, 500, 500]
    elif failure == "timeout":

        def raise_timeout(request, timeout=None):
            raise TimeoutError(f"timed out while calling {request.full_url}")

        clean_env.setattr(healthcheck.urllib.request, "urlopen", raise_timeout)
    else:
        clean_env.setenv("HEALTHCHECK_BASE_URL", _closed_port_url())

        def refuse(request, timeout=None):
            raise urllib.error.URLError(f"refused {request.full_url}")

        # Real refused connection first; a second check uses the URL-in-text error.
        rc = healthcheck.main(["success", "hi"])
        captured = capsys.readouterr()
        assert rc == 0
        assert captured.err.strip() == WARNING
        assert KEY not in captured.out + captured.err
        clean_env.setattr(healthcheck.urllib.request, "urlopen", refuse)
    rc = healthcheck.main(["fail", "hi"])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err.strip() == WARNING
    assert KEY not in captured.out + captured.err


def test_error_text_with_url_is_not_printed(server, clean_env, capsys):
    def explode(request, timeout=None):
        raise RuntimeError(f"boom {request.full_url} {KEY}")

    clean_env.setattr(healthcheck.urllib.request, "urlopen", explode)
    rc = healthcheck.main(["success", "hi"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "boom" not in captured.out + captured.err
    assert "127.0.0.1" not in captured.out + captured.err


def test_timeout_is_set(clean_env):
    clean_env.setenv("HEALTHCHECK_PING_KEY", KEY)
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        seen.append(timeout)
        return Response()

    clean_env.setattr(healthcheck.urllib.request, "urlopen", fake_urlopen)
    assert healthcheck.main(["success", "hi"]) == 0
    assert seen == [10]


@pytest.mark.parametrize(
    "argv", [["bogus", "x"], [], ["success"], ["success", "a", "b"]]
)
def test_unknown_status_exits_nonzero(server, capsys, argv):
    rc = healthcheck.main(argv)
    assert rc != 0
    assert "Usage" in capsys.readouterr().err
    assert server.requests == []


def test_script_exit_codes_and_no_key_in_output(server):
    """Run the script as the pipeline does. A dead server still gives exit 0."""
    env = {
        "PATH": os.environ["PATH"],
        "HEALTHCHECK_PING_KEY": KEY,
        "HEALTHCHECK_BASE_URL": server.base,
    }
    ok = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "healthcheck.py"), "success", "hi"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert ok.returncode == 0
    assert [r["path"] for r in server.requests] == [f"/{KEY}/the-sommelier"]

    env["HEALTHCHECK_BASE_URL"] = _closed_port_url()
    dead = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "healthcheck.py"), "fail", "hi"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert dead.returncode == 0
    assert dead.stderr.strip() == WARNING
    assert KEY not in dead.stdout + dead.stderr

    bad = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "healthcheck.py"), "bogus", "x"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert bad.returncode == 2


@pytest.mark.parametrize(
    "base", ["http://[bad", "https://h:99999", "https://h/?q=", "https://h/#x"]
)
@pytest.mark.parametrize("status", ["success", "fail"])
def test_malformed_base_url_exits_zero_and_makes_no_request(
    server, clean_env, capsys, monkeypatch, base, status
):
    def no_network(request, timeout=None):
        raise AssertionError("a request was made")

    clean_env.setenv("HEALTHCHECK_BASE_URL", base)
    monkeypatch.setattr(healthcheck.urllib.request, "urlopen", no_network)
    assert healthcheck.main([status, "hi"]) == 0
    captured = capsys.readouterr()
    assert WARNING not in captured.err
    assert KEY not in captured.out + captured.err
    assert server.requests == []


@pytest.mark.parametrize("base", ["http://[bad", "https://h:99999"])
def test_script_with_malformed_base_url_exits_zero_without_traceback(server, base):
    env = {
        "PATH": os.environ["PATH"],
        "HEALTHCHECK_PING_KEY": KEY,
        "HEALTHCHECK_BASE_URL": base,
    }
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "healthcheck.py"), "fail", "hi"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    assert KEY not in result.stdout + result.stderr
    assert server.requests == []
