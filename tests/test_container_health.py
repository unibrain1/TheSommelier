"""Tests for container_health.py — the site check and the plan.json age check."""

import http.server
import os
import socket
import threading
import time

import pytest

import container_health
import settings


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for name in settings.ALL_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SITE_DIR", str(tmp_path))
    return monkeypatch


@pytest.fixture
def site():
    """A local HTTP server that gives 200 for /index.html and 404 for all else."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            status = 200 if self.path == "/index.html" else 404
            self.send_response(status)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


def write_plan(site_dir, age_minutes=0.0):
    plan = site_dir / "plan.json"
    plan.write_text("{}", encoding="utf-8")
    mtime = time.time() - age_minutes * 60
    os.utime(plan, (mtime, mtime))
    return plan


def closed_port_url():
    # Bind and release a port, so nothing listens on it.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}/index.html"


def test_fresh_plan_and_site_up_exits_0(clean_env, tmp_path, site, capsys):
    write_plan(tmp_path)
    assert container_health.main(url=f"{site}/index.html") == 0
    assert capsys.readouterr().out == ""


def test_old_plan_exits_1_and_names_the_age(clean_env, tmp_path, site, capsys):
    write_plan(tmp_path, age_minutes=700)
    assert container_health.main(url=f"{site}/index.html") == 1
    out = capsys.readouterr().out
    assert "700 minutes old" in out
    assert "limit is 600" in out
    assert out.count("\n") == 1


def test_missing_plan_exits_1(clean_env, tmp_path, site, capsys):
    assert container_health.main(url=f"{site}/index.html") == 1
    out = capsys.readouterr().out
    assert "plan.json is missing" in out
    assert out.count("\n") == 1


def test_site_down_exits_1_and_names_the_url(clean_env, tmp_path, capsys):
    write_plan(tmp_path)
    url = closed_port_url()
    assert container_health.main(url=url) == 1
    out = capsys.readouterr().out
    assert url in out
    assert out.count("\n") == 1


def test_site_404_exits_1_and_names_the_url(clean_env, tmp_path, site, capsys):
    write_plan(tmp_path)
    url = f"{site}/missing.html"
    assert container_health.main(url=url) == 1
    out = capsys.readouterr().out
    assert url in out
    assert "404" in out
    assert out.count("\n") == 1


def test_health_max_age_minutes_changes_the_limit(clean_env, tmp_path, site, capsys):
    write_plan(tmp_path, age_minutes=10)
    assert container_health.main(url=f"{site}/index.html") == 0
    clean_env.setenv("HEALTH_MAX_AGE_MINUTES", "5")
    assert container_health.main(url=f"{site}/index.html") == 1
    assert "The limit is 5." in capsys.readouterr().out


def test_default_url_is_nginx_in_the_container():
    assert container_health.NGINX_INDEX_URL == "http://127.0.0.1:8080/index.html"
    # SITE_URL is a setting. The local name must not look like one.
    assert not hasattr(container_health, "SITE_URL")


def test_plan_json_that_is_a_folder_is_not_healthy(clean_env, site, tmp_path, capsys):
    (tmp_path / "plan.json").mkdir()
    assert container_health.main(f"{site}/index.html") == 1
    assert "is not a file" in capsys.readouterr().out


def test_reply_that_is_not_http_gives_one_line(clean_env, tmp_path, capsys):
    write_plan(tmp_path)
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        def reply():
            conn, _ = server.accept()
            with conn:
                conn.recv(1024)
                conn.sendall(b"NOT HTTP\r\n\r\n")

        thread = threading.Thread(target=reply, daemon=True)
        thread.start()
        assert container_health.main(f"http://127.0.0.1:{port}/index.html") == 1
        thread.join(5)
    assert capsys.readouterr().out.count("\n") == 1
