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
UUID = "5f2a9c1e-7b3d-4e8f-a1c2-SECRET-canary"

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
    clean_env.setenv("HEALTHCHECK_URL", f"{srv.base}/{KEY}/the-sommelier")
    yield srv
    srv.close()


def test_success_posts_to_the_url(server):
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


@pytest.mark.parametrize(
    "path",
    [f"/{KEY}/my-check", f"/{UUID}", f"/ping/{UUID}"],
    ids=["key_and_slug", "uuid", "path_prefix"],
)
def test_each_url_form_is_pinged_as_given(server, clean_env, path):
    clean_env.setenv("HEALTHCHECK_URL", server.base + path + "/")
    assert healthcheck.main(["success", "hi"]) == 0
    assert healthcheck.main(["fail", "hi"]) == 0
    assert [r["path"] for r in server.requests] == [path, path + "/fail"]


def test_old_variables_have_no_effect(server, clean_env):
    clean_env.setenv("HEALTHCHECK_ENABLED", "false")
    clean_env.setenv("HEALTHCHECK_SLUG", "other")
    clean_env.setenv("HEALTHCHECK_BASE_URL", "http://127.0.0.1:1")
    clean_env.setenv("HEALTHCHECK_PING_KEY", "other-key")
    assert healthcheck.main(["success", "hi"]) == 0
    assert [r["path"] for r in server.requests] == [f"/{KEY}/the-sommelier"]


@pytest.mark.parametrize("case", ["no_url", "op_reference", "invalid_scheme"])
def test_off_makes_no_request(server, clean_env, capsys, case):
    if case == "no_url":
        clean_env.delenv("HEALTHCHECK_URL")
        # An old variable alone does not turn the feature on.
        clean_env.setenv("HEALTHCHECK_PING_KEY", KEY)
    elif case == "op_reference":
        clean_env.setenv("HEALTHCHECK_URL", "op://vault/item/url")
    else:
        clean_env.setenv("HEALTHCHECK_URL", f"ftp://127.0.0.1/{KEY}")
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
    assert captured.err.strip() == f"{WARNING} (HTTPError HTTP 500)"


def _closed_port_url():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}/{KEY}/the-sommelier"


@pytest.mark.parametrize("failure", ["http_error", "timeout", "connection_refused"])
def test_key_never_in_output(server, clean_env, capsys, failure):
    if failure == "http_error":
        server.statuses = [500, 500, 500]
    elif failure == "timeout":

        def raise_timeout(request, timeout=None):
            raise TimeoutError(f"timed out while calling {request.full_url}")

        clean_env.setattr(healthcheck.urllib.request, "urlopen", raise_timeout)
    else:
        clean_env.setenv("HEALTHCHECK_URL", _closed_port_url())

        def refuse(request, timeout=None):
            raise urllib.error.URLError(f"refused {request.full_url}")

        # Real refused connection first; a second check uses the URL-in-text error.
        rc = healthcheck.main(["success", "hi"])
        captured = capsys.readouterr()
        assert rc == 0
        assert captured.err.strip() == f"{WARNING} (URLError)"
        assert KEY not in captured.out + captured.err
        clean_env.setattr(healthcheck.urllib.request, "urlopen", refuse)
    rc = healthcheck.main(["fail", "hi"])
    captured = capsys.readouterr()
    assert rc == 0
    expected = {
        "http_error": "HTTPError HTTP 500",
        "timeout": "TimeoutError",
        "connection_refused": "URLError",
    }[failure]
    assert captured.err.strip() == f"{WARNING} ({expected})"
    assert KEY not in captured.out + captured.err


def test_http_error_prints_class_and_code_only(server, clean_env, capsys):
    url = f"{server.base}/{KEY}/the-sommelier"

    def not_found(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 404, f"no {url}", {}, None)

    clean_env.setattr(healthcheck.urllib.request, "urlopen", not_found)
    assert healthcheck.main(["fail", "hi"]) == 0
    captured = capsys.readouterr()
    assert captured.err.strip() == f"{WARNING} (HTTPError HTTP 404)"
    assert KEY not in captured.out + captured.err
    assert "127.0.0.1" not in captured.out + captured.err


def test_url_error_prints_class_only(server, clean_env, capsys):
    def refuse(request, timeout=None):
        raise urllib.error.URLError(f"refused {request.full_url}")

    clean_env.setattr(healthcheck.urllib.request, "urlopen", refuse)
    assert healthcheck.main(["fail", "hi"]) == 0
    captured = capsys.readouterr()
    assert captured.err.strip() == f"{WARNING} (URLError)"
    assert KEY not in captured.out + captured.err
    assert "127.0.0.1" not in captured.out + captured.err


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
    clean_env.setenv("HEALTHCHECK_URL", f"https://hc-ping.com/{KEY}")
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
        "HEALTHCHECK_URL": f"{server.base}/{KEY}/the-sommelier",
    }
    ok = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "healthcheck.py"), "success", "hi"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert ok.returncode == 0
    assert [r["path"] for r in server.requests] == [f"/{KEY}/the-sommelier"]

    env["HEALTHCHECK_URL"] = _closed_port_url()
    dead = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "healthcheck.py"), "fail", "hi"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert dead.returncode == 0
    assert dead.stderr.strip() == f"{WARNING} (URLError)"
    assert KEY not in dead.stdout + dead.stderr

    bad = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "healthcheck.py"), "bogus", "x"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert bad.returncode == 2


@pytest.mark.parametrize(
    "url",
    [
        f"http://[bad/{KEY}",
        f"https://h:99999/{KEY}",
        f"https://h/{KEY}?q=",
        f"https://h/{KEY}#x",
    ],
)
@pytest.mark.parametrize("status", ["success", "fail"])
def test_malformed_url_exits_zero_and_makes_no_request(
    server, clean_env, capsys, monkeypatch, url, status
):
    def no_network(request, timeout=None):
        raise AssertionError("a request was made")

    clean_env.setenv("HEALTHCHECK_URL", url)
    monkeypatch.setattr(healthcheck.urllib.request, "urlopen", no_network)
    assert healthcheck.main([status, "hi"]) == 0
    captured = capsys.readouterr()
    assert WARNING not in captured.err
    assert KEY not in captured.out + captured.err
    assert server.requests == []


@pytest.mark.parametrize("url", [f"http://[bad/{KEY}", f"https://h:99999/{KEY}"])
def test_script_with_malformed_url_exits_zero_without_traceback(server, url):
    env = {"PATH": os.environ["PATH"], "HEALTHCHECK_URL": url}
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
