"""Run the real shell pipeline against a fake `curl`. No network, no `claude`.

Each test copies the shell scripts and `scripts/` into `tmp_path/app`, so the
real `data/` and `site/` folders stay as they are. The fake `curl` copies a
fixture file and logs each URL. A local HTTP server stands in for
Healthchecks.io and for the community notes feed (that script uses urllib,
not curl).
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.helpers import (
    CONSUMED_HEADER,
    FOODTAGS_HEADER,
    NOTES_HEADER,
    build_community_rss,
    build_header_only_tsv,
    build_ics,
    build_inventory_tsv,
    ha_rest_sensors,
)

REPO = Path(__file__).resolve().parent.parent
SHELL_SCRIPTS = ("pipeline.sh", "fetch.sh", "fetch_docker.sh", "send_digest.sh")
KEY_CANARY = "canary-key-7f3a9c1e5b"
FAKE_USER = "fake-user"
FAKE_PASSWORD = "fake-password-canary"
# 6 rows x 14 = 84 bottles of 5 wines: enough for 52 weekly slots.
BOTTLE_COPIES = 14
BOTTLES = 6 * BOTTLE_COPIES
ICS_URL = "https://calendar.example.test/private/menu.ics"

# Reads -o and the URL like the real call does, logs the URL, and copies the
# fixture. A missing fixture is an HTTP error for `curl -f`: exit 22. Without
# -g, real curl reads [] and {} as a glob and fails a URL with them: exit 3.
FAKE_CURL = """#!/usr/bin/env bash
out=""
url=""
globoff=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -*g*) globoff=1; shift ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
echo "${url}" >> "${FAKE_CURL_LOG}"
[[ ${globoff} -eq 1 || "${url}" != *[\\[\\]\\{\\}]* ]] || exit 3
case "${url}" in
  *Table=Inventory*) name=inventory.tsv ;;
  *Table=Notes*) name=notes.tsv ;;
  *Table=FoodTags*) name=foodtags.tsv ;;
  *Table=Consumed*) name=consumed.tsv ;;
  *) name=menu.ics ;;
esac
[[ -f "${FAKE_CURL_FIXTURES}/${name}" ]] || exit 22
cp "${FAKE_CURL_FIXTURES}/${name}" "${out}"
"""


class LocalServer:
    """Records each POST (Healthchecks.io stand-in). Serves a feed on GET /rss."""

    def __init__(self, rss: str) -> None:
        self.posts: list[tuple[str, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8", errors="replace")
                outer.posts.append((self.path, body))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")

            def do_GET(self) -> None:
                if self.path != "/rss":
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/rss+xml")
                self.end_headers()
                self.wfile.write(rss.encode("utf-8"))

            def log_message(self, *args: object) -> None:
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    @property
    def paths(self) -> list[str]:
        return [p for p, _ in self.posts]


@dataclass
class Harness:
    root: Path
    app: Path
    fixtures: Path
    curl_log: Path
    path: str
    server: LocalServer
    env: dict[str, str] = field(default_factory=dict)

    def curl_urls(self) -> list[str]:
        if not self.curl_log.exists():
            return []
        return self.curl_log.read_text().splitlines()

    def run(
        self,
        script: str,
        args: tuple[str, ...] = (),
        extra: dict[str, str] | None = None,
        drop: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess:
        env = {**self.env, **(extra or {})}
        for name in drop:
            env.pop(name, None)
        return subprocess.run(
            ["bash", script, *args],
            cwd=self.app,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )


def _snapshot(folder: Path) -> dict[str, int] | None:
    """Return {relative path: mtime in ns} for a folder, or None if it is absent."""
    if not folder.exists():
        return None
    return {str(p.relative_to(folder)): p.stat().st_mtime_ns for p in folder.rglob("*")}


@pytest.fixture(autouse=True)
def repo_data_and_site_unchanged():
    """A pipeline run in the copy must not touch the real data/ and site/."""
    before = {name: _snapshot(REPO / name) for name in ("data", "site")}
    yield
    after = {name: _snapshot(REPO / name) for name in ("data", "site")}
    assert after == before


@pytest.fixture
def server():
    srv = LocalServer(build_community_rss())
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture
def harness(tmp_path, server) -> Harness:
    jq = shutil.which("jq")
    if jq is None:
        pytest.skip("jq is not installed")

    app = tmp_path / "app"
    app.mkdir()
    for name in SHELL_SCRIPTS:
        shutil.copy2(REPO / name, app / name)
    shutil.copytree(
        REPO / "scripts",
        app / "scripts",
        # A v3 plan_config.py of the owner can still be in scripts/.
        ignore=shutil.ignore_patterns("__pycache__", "plan_config.py"),
    )
    # The sample, not the personal config/plan.yaml of the owner.
    (app / "config").mkdir()
    shutil.copy2(REPO / "config" / "plan.yaml.sample", app / "config" / "plan.yaml")

    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    build_inventory_tsv(fixtures / "inventory.tsv", copies=BOTTLE_COPIES)
    build_header_only_tsv(fixtures / "notes.tsv", NOTES_HEADER)
    build_header_only_tsv(fixtures / "foodtags.tsv", FOODTAGS_HEADER)
    build_header_only_tsv(fixtures / "consumed.tsv", CONSUMED_HEADER)
    (fixtures / "menu.ics").write_text(build_ics(), encoding="utf-8")

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    curl = fake_bin / "curl"
    curl.write_text(FAKE_CURL)
    curl.chmod(0o755)
    # A link to `jq` only. Its own folder can also hold `claude`.
    (fake_bin / "jq").symlink_to(jq)

    venv_bin = Path(sys.executable).parent
    if not (venv_bin / "python3").exists():
        pytest.skip(f"no python3 next to {sys.executable}")
    path = os.pathsep.join([str(fake_bin), str(venv_bin), "/usr/bin", "/bin"])
    if shutil.which("claude", path=path) is not None:
        pytest.skip("a claude CLI is on the test PATH, the tests need none")

    curl_log = tmp_path / "curl.log"
    env = {
        "PATH": path,
        "HOME": str(tmp_path / "home"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "FAKE_CURL_LOG": str(curl_log),
        "FAKE_CURL_FIXTURES": str(fixtures),
        "USERNAME": FAKE_USER,
        "PASSWORD": FAKE_PASSWORD,
        "GOOGLE_CALENDAR_ICS_URL": ICS_URL,
        "DATA_DIR": str(tmp_path / "run" / "data"),
        "SITE_DIR": str(tmp_path / "run" / "data" / "site"),
        "PLAN_CONFIG": str(app / "config" / "plan.yaml"),
    }
    return Harness(tmp_path, app, fixtures, curl_log, path, server, env)


def _output(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _enable_healthcheck(h: Harness) -> dict[str, str]:
    return {
        "HEALTHCHECK_URL": f"{h.server.base_url}/{KEY_CANARY}/the-sommelier",
    }


def _assert_site_files(site: Path) -> None:
    for name in (
        "plan.json",
        "pairing_suggestions.json",
        "report.json",
    ):
        assert (site / name).is_file(), f"{name} is missing in {site}"


def test_fixtures_parse_as_expected(harness):
    """Guard the fixtures: the later tests depend on two meals and six bottles."""
    result = subprocess.run(
        [
            sys.executable,
            "scripts/parse_menu.py",
            str(harness.fixtures / "menu.ics"),
        ],
        cwd=harness.app,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, _output(result)
    meals = json.loads(result.stdout)
    assert [m["meal"] for m in meals] == [
        "Grilled Steak with Roasted Potatoes",
        "Lemon Chicken Piccata",
    ]


def test_pipeline_runs_with_tmp_data_and_site_dirs(harness):
    data = Path(harness.env["DATA_DIR"])
    site = Path(harness.env["SITE_DIR"])
    assert not data.exists() and not site.exists()

    result = harness.run("fetch_docker.sh")

    assert result.returncode == 0, _output(result)
    # The fake ran: one call for each of the five fetches.
    urls = harness.curl_urls()
    assert len(urls) == 5, urls
    plan = json.loads((site / "plan.json").read_text())
    assert len(plan["allWeeks"]) == 52
    _assert_site_files(site)
    assert (data / "inventory.json").is_file()
    report = json.loads((site / "report.json").read_text())
    assert report["summary"]["inventory_total_bottles"] == BOTTLES
    assert "Skipping note generation" in result.stdout
    assert "Skipping menu enrichment" in result.stdout
    assert "Pairing summary:" in result.stdout
    # The app copy keeps no run output.
    assert not (harness.app / "data").exists()
    assert not (harness.app / "site").exists()


def test_pipeline_site_dir_outside_data_dir(harness, tmp_path):
    data = Path(harness.env["DATA_DIR"])
    site = tmp_path / "elsewhere" / "public" / "site"

    result = harness.run(
        "fetch_docker.sh", extra={"SITE_DIR": str(site), "HA_ENABLED": "true"}
    )

    assert result.returncode == 0, _output(result)
    _assert_site_files(site)
    assert not (data / "site").exists()
    assert (data / "inventory.json").is_file()
    # The run output stays out of the data folder: it holds only staged files.
    for name in ("thisweek.json", "today.json"):
        assert (site / name).is_file()
        assert not (data / name).exists()
    assert not (site / "inventory.json").exists()


@pytest.mark.parametrize("missing", ["USERNAME", "PASSWORD"])
def test_pipeline_missing_required_exits_nonzero(harness, missing):
    result = harness.run("fetch_docker.sh", drop=(missing,))

    assert result.returncode != 0, _output(result)
    assert missing in result.stderr, _output(result)
    # The settings check stops the run before any fetch.
    assert harness.curl_urls() == []
    assert not Path(harness.env["DATA_DIR"]).exists()


def test_failed_run_leaves_published_plan_unchanged(harness):
    # container_health.py reads the mtime of plan.json as the time of the last
    # complete run. A run that fails before the publish must not touch it.
    plan = Path(harness.env["SITE_DIR"]) / "plan.json"
    first = harness.run("fetch_docker.sh")
    assert first.returncode == 0, _output(first)
    before = plan.stat().st_mtime_ns
    (harness.app / "scripts" / "pairing.py").write_text("raise SystemExit(1)\n")

    second = harness.run("fetch_docker.sh")

    assert second.returncode != 0, _output(second)
    assert "Pairing failed" in second.stdout
    assert plan.stat().st_mtime_ns == before


def test_pipeline_failure_sends_fail_ping(harness):
    (harness.fixtures / "inventory.tsv").unlink()

    result = harness.run("fetch_docker.sh", extra=_enable_healthcheck(harness))

    assert result.returncode != 0, _output(result)
    # The fake ran, and it failed the inventory fetch.
    assert any("Table=Inventory" in u for u in harness.curl_urls())
    assert harness.server.paths == [f"/{KEY_CANARY}/the-sommelier/fail"]
    assert KEY_CANARY not in result.stdout
    assert KEY_CANARY not in result.stderr


def test_pipeline_settings_failure_sends_fail_ping(harness):
    """A run that stops at the settings check still sends the fail ping."""
    result = harness.run(
        "fetch_docker.sh", extra=_enable_healthcheck(harness), drop=("PASSWORD",)
    )

    assert result.returncode != 0, _output(result)
    assert harness.server.paths == [f"/{KEY_CANARY}/the-sommelier/fail"]
    # No fetch ran.
    assert harness.curl_urls() == []
    assert KEY_CANARY not in result.stdout
    assert KEY_CANARY not in result.stderr


def test_pipeline_success_sends_ping(harness):
    result = harness.run("fetch_docker.sh", extra=_enable_healthcheck(harness))

    assert result.returncode == 0, _output(result)
    assert harness.curl_urls()
    assert harness.server.paths == [f"/{KEY_CANARY}/the-sommelier"]
    assert "healthcheck: on" in result.stdout
    body = harness.server.posts[0][1]
    assert f"Bottles: {BOTTLES}" in body
    assert "Weeks planned: 52" in body
    assert KEY_CANARY not in result.stdout
    assert KEY_CANARY not in result.stderr


def test_pipeline_ignores_the_old_healthcheck_variables(harness):
    old = {
        "HEALTHCHECK_PING_KEY": KEY_CANARY,
        "HEALTHCHECK_SLUG": "the-sommelier",
        "HEALTHCHECK_BASE_URL": harness.server.base_url,
        "HEALTHCHECK_ENABLED": "true",
    }
    result = harness.run("fetch_docker.sh", extra=old)

    assert result.returncode == 0, _output(result)
    assert harness.server.paths == []
    assert "healthcheck: off (not configured)" in result.stdout
    assert "HEALTHCHECK_" not in result.stdout + result.stderr


def test_pipeline_community_notes_off_and_on(harness):
    data = Path(harness.env["DATA_DIR"])

    off = harness.run("fetch_docker.sh")
    assert off.returncode == 0, _output(off)
    assert "community notes feature is off" in off.stdout
    assert not (data / "community_notes.json").exists()

    # The removed flag has no effect: the URL alone turns the fetch on.
    on = harness.run(
        "fetch_docker.sh",
        extra={
            "CT_COMMUNITY_NOTES_RSS": f"{harness.server.base_url}/rss",
            "COMMUNITY_NOTES_ENABLED": "false",
        },
    )
    assert on.returncode == 0, _output(on)
    assert "Fetching community tasting notes" in on.stdout
    assert "COMMUNITY_NOTES_ENABLED is set. The flag was removed." in _output(on)
    cache = json.loads((data / "community_notes.json").read_text())
    assert list(cache) == ["200001"]
    assert cache["200001"][0]["score"] == 93


def test_fetch_sh_runs_locally(harness):
    (harness.app / ".env").write_text(
        f"USERNAME='{FAKE_USER}'\n"
        f"PASSWORD='{FAKE_PASSWORD}'\n"
        f"GOOGLE_CALENDAR_ICS_URL='{ICS_URL}'\n"
    )

    result = harness.run(
        "fetch.sh",
        drop=(
            "USERNAME",
            "PASSWORD",
            "GOOGLE_CALENDAR_ICS_URL",
            "DATA_DIR",
            "SITE_DIR",
        ),
    )

    assert result.returncode == 0, _output(result)
    assert any(f"User={FAKE_USER}&" in u for u in harness.curl_urls())
    assert (harness.app / "data" / "inventory.json").is_file()
    _assert_site_files(harness.app / "site")
    assert "inventory_total_bottles" in result.stdout


def test_fetch_sh_reads_plan_yaml_of_the_repo(harness):
    # A field that is not valid shows which file fetch.sh gave to the plan step.
    (harness.app / "config" / "plan.yaml").write_text("holidays: 1\n")
    (harness.app / ".env").write_text(
        f"USERNAME='{FAKE_USER}'\n"
        f"PASSWORD='{FAKE_PASSWORD}'\n"
        f"GOOGLE_CALENDAR_ICS_URL='{ICS_URL}'\n"
    )

    result = harness.run(
        "fetch.sh",
        drop=(
            "USERNAME",
            "PASSWORD",
            "GOOGLE_CALENDAR_ICS_URL",
            "DATA_DIR",
            "SITE_DIR",
            "PLAN_CONFIG",
        ),
    )

    assert result.returncode != 0, _output(result)
    output = _output(result)
    assert f"{harness.app / 'config' / 'plan.yaml'}: holidays: must be a list" in output
    assert "Plan generation failed" in output


def test_fetch_sh_does_not_override_env_with_dotenv(harness):
    (harness.app / ".env").write_text(
        "USERNAME='fromfile'\nPASSWORD='fromfile-pw'\nGOOGLE_CALENDAR_ICS_URL='https://file.example.test/x.ics'\n"
    )

    # fetch.sh skips .env only when both required values are in the env.
    result = harness.run(
        "fetch.sh",
        extra={
            "USERNAME": "fromenv",
            "PASSWORD": "fromenv-pw",
            "GOOGLE_CALENDAR_ICS_URL": "https://env.example.test/x.ics",
        },
    )

    assert result.returncode == 0, _output(result)
    urls = harness.curl_urls()
    assert urls
    assert any("User=fromenv&Password=fromenv-pw" in u for u in urls)
    assert "https://env.example.test/x.ics" in urls
    assert not any("fromfile" in u or "file.example.test" in u for u in urls)


@pytest.mark.parametrize("missing", ["USERNAME", "PASSWORD"])
def test_fetch_sh_reads_dotenv_when_one_required_value_is_missing(harness, missing):
    """One of two values in the shell does not skip .env: the file is sourced."""
    (harness.app / ".env").write_text(
        "USERNAME='fromfile'\nPASSWORD='fromfile-pw'\nGOOGLE_CALENDAR_ICS_URL='https://file.example.test/x.ics'\n"
    )

    result = harness.run(
        "fetch.sh",
        extra={
            "USERNAME": "fromenv",
            "PASSWORD": "fromenv-pw",
            "GOOGLE_CALENDAR_ICS_URL": "https://env.example.test/x.ics",
        },
        drop=(missing,),
    )

    assert result.returncode == 0, _output(result)
    urls = harness.curl_urls()
    assert urls
    # The file is sourced after the env, so the file value wins for every name.
    assert any("User=fromfile&Password=fromfile-pw" in u for u in urls)
    assert "https://file.example.test/x.ics" in urls


def test_fetch_sh_with_only_username_in_env_reads_dotenv(harness):
    (harness.app / ".env").write_text(
        "USERNAME='fromfile'\nPASSWORD='fromfile-pw'\nGOOGLE_CALENDAR_ICS_URL='https://file.example.test/x.ics'\n"
    )

    result = harness.run(
        "fetch.sh",
        extra={"USERNAME": "fromenv"},
        drop=("PASSWORD",),
    )

    assert result.returncode == 0, _output(result)
    urls = harness.curl_urls()
    assert any("User=fromfile&Password=fromfile-pw" in u for u in urls)
    assert not any("fromenv" in u for u in urls)


def test_fetch_sh_with_username_and_password_in_env_does_not_read_dotenv(harness):
    # The calendar URL is only in .env. If fetch.sh read the file, the menu
    # would turn on and curl would fetch the calendar.
    (harness.app / ".env").write_text(
        f"USERNAME='fromfile'\nPASSWORD='fromfile-pw'\n"
        f"GOOGLE_CALENDAR_ICS_URL='{ICS_URL}'\n"
    )

    result = harness.run(
        "fetch.sh",
        extra={"USERNAME": "fromenv", "PASSWORD": "fromenv-pw"},
        drop=("GOOGLE_CALENDAR_ICS_URL", "DATA_DIR", "SITE_DIR"),
    )

    assert result.returncode == 0, _output(result)
    urls = harness.curl_urls()
    assert any("User=fromenv&Password=fromenv-pw" in u for u in urls)
    assert not any("fromfile" in u for u in urls)
    assert ICS_URL not in urls
    assert "menu: off (not configured)" in result.stdout
    assert "Pairing: menu feature is off" in result.stdout
    assert not (harness.app / "data" / "pairing_suggestions.json").exists()
    assert not (harness.app / "site" / "pairing_suggestions.json").exists()


def test_fetch_sh_with_menu_on_prints_pairing_summary(harness):
    result = harness.run("fetch.sh")

    assert result.returncode == 0, _output(result)
    assert "Pairing summary:" in result.stdout
    assert "Pairing: menu feature is off" not in result.stdout


def test_send_digest_sh_dry_run_without_op(harness):
    run = harness.run("fetch_docker.sh")
    assert run.returncode == 0, _output(run)
    (harness.app / ".env").write_text(
        "SMTP_USERNAME='sender@example.test'\n"
        "SMTP_PASSWORD='smtp-pw-canary'\n"
        "DIGEST_RECIPIENTS='a@example.test,b@example.test'\n"
    )

    result = harness.run("send_digest.sh", args=("--dry-run",))

    assert result.returncode == 0, _output(result)
    assert "=== DRY RUN ===" in result.stdout
    assert "From: sender@example.test" in result.stdout
    assert "To: a@example.test, b@example.test" in result.stdout
    assert "smtp-pw-canary" not in result.stdout + result.stderr


def test_send_digest_sh_with_dotenv_reads_site_folder_of_the_app(harness):
    """With a .env and no DATA_DIR or SITE_DIR, the folders are <app>/data and <app>/site."""
    site = harness.app / "site"
    site.mkdir()
    html = "<html>" + "x" * 94 + "</html>"
    (site / "digest.html").write_text(html, encoding="utf-8")
    (site / "digest.json").write_text(
        json.dumps({"date": "2000-01-01", "has_content": True, "wine": None}),
        encoding="utf-8",
    )
    (harness.app / ".env").write_text(
        "SMTP_USERNAME='sender@example.test'\n"
        "SMTP_PASSWORD='smtp-pw-canary'\n"
        "DIGEST_RECIPIENTS='a@example.test'\n"
    )

    result = harness.run(
        "send_digest.sh",
        args=("--dry-run", "--force"),
        drop=("DATA_DIR", "SITE_DIR"),
    )

    assert result.returncode == 0, _output(result)
    assert "=== DRY RUN ===" in result.stdout
    assert f"Body: {len(html)} bytes HTML" in result.stdout
    assert "smtp-pw-canary" not in result.stdout + result.stderr

    # Without the file in <app>/site, the same call fails and names the path.
    (site / "digest.json").unlink()
    missing = harness.run(
        "send_digest.sh",
        args=("--dry-run", "--force"),
        drop=("DATA_DIR", "SITE_DIR"),
    )
    assert missing.returncode == 1, _output(missing)
    assert str(site / "digest.json") in missing.stderr


def test_send_digest_sh_does_not_override_env_with_dotenv(harness):
    site = Path(harness.env["SITE_DIR"])
    site.mkdir(parents=True)
    (site / "digest.html").write_text("<html>digest</html>", encoding="utf-8")
    (site / "digest.json").write_text(
        json.dumps({"date": "2000-01-01", "has_content": True, "wine": None}),
        encoding="utf-8",
    )
    (harness.app / ".env").write_text(
        "SMTP_USERNAME='fromfile@example.com'\n"
        "SMTP_PASSWORD='fromfile-pw'\n"
        "DIGEST_RECIPIENTS='file@example.com'\n"
    )

    result = harness.run(
        "send_digest.sh",
        args=("--dry-run", "--force"),
        extra={
            "SMTP_USERNAME": "fromenv@example.com",
            "SMTP_PASSWORD": "fromenv-pw",
            "DIGEST_RECIPIENTS": "env@example.com",
        },
    )

    assert result.returncode == 0, _output(result)
    assert "From: fromenv@example.com" in result.stdout
    assert "To: env@example.com" in result.stdout
    assert "fromfile" not in result.stdout + result.stderr


def test_send_digest_sh_without_dotenv_uses_repo_site_folder(harness):
    """On the host, fetch.sh writes to the repo folders with or without .env."""
    site = harness.app / "site"
    site.mkdir(exist_ok=True)
    (site / "digest.html").write_text("<html>digest</html>", encoding="utf-8")
    (site / "digest.json").write_text(
        json.dumps({"date": "2000-01-01", "has_content": True, "wine": None}),
        encoding="utf-8",
    )
    assert not (harness.app / ".env").exists()

    result = harness.run(
        "send_digest.sh",
        args=("--dry-run", "--force"),
        extra={"SMTP_USERNAME": "fromenv@example.com"},
        drop=("DATA_DIR", "SITE_DIR"),
    )

    assert result.returncode == 0, _output(result)
    assert "=== DRY RUN ===" in result.stdout


def test_pipeline_failure_after_success_ping_sends_no_fail_ping(harness):
    """The wrapper runs `jq` after the success ping. Its failure is not a pipeline failure."""
    jq = harness.root / "fakebin" / "jq"
    jq.unlink()
    jq.write_text("#!/usr/bin/env bash\nexit 1\n")
    jq.chmod(0o755)

    result = harness.run("fetch_docker.sh", extra=_enable_healthcheck(harness))

    # The wrapper failed after the pipeline finished.
    assert result.returncode != 0, _output(result)
    assert "Sync complete" in result.stdout
    assert harness.server.paths == [f"/{KEY_CANARY}/the-sommelier"]


def _stub_claude(root: Path) -> str:
    """Return a PATH folder that holds a `claude` that always fails.

    generate_notes.py and enrich_menu.py treat a failing CLI as non-fatal.
    """
    claude_bin = root / "claudebin"
    claude_bin.mkdir()
    stub = claude_bin / "claude"
    stub.write_text("#!/usr/bin/env bash\nexit 1\n")
    stub.chmod(0o755)
    return str(claude_bin)


@pytest.mark.parametrize(
    ("key", "mode"),
    [
        ("CLAUDE_CODE_OAUTH_TOKEN", "subscription"),
        ("ANTHROPIC_API_KEY", "API key"),
    ],
)
def test_pipeline_claude_gate_on_runs_both_steps(harness, key, mode):
    path = _stub_claude(harness.root) + os.pathsep + harness.path

    result = harness.run(
        "fetch_docker.sh",
        extra={"PATH": path, key: "key-canary-9d2c"},
    )

    assert result.returncode == 0, _output(result)
    assert f"claude: on ({mode})" in result.stdout + result.stderr
    assert "Generating tasting notes" in result.stdout
    assert "Enriching menu entries" in result.stdout
    assert "Skipping note generation" not in result.stdout
    assert "key-canary-9d2c" not in result.stdout + result.stderr
    _assert_site_files(Path(harness.env["SITE_DIR"]))


def test_pipeline_claude_gate_off_when_both_keys_set(harness):
    """A wrong key setting turns notes off, but the plan still publishes."""
    path = _stub_claude(harness.root) + os.pathsep + harness.path

    result = harness.run(
        "fetch_docker.sh",
        extra={
            "PATH": path,
            "CLAUDE_CODE_OAUTH_TOKEN": "oauth-canary-9d2c",
            "ANTHROPIC_API_KEY": "apikey-canary-4f1a",
        },
    )

    assert result.returncode == 0, _output(result)
    assert "Sync complete" in result.stdout
    assert "Claude feature is off" in result.stdout
    # The operator must see why the notes are off.
    output = result.stdout + result.stderr
    assert "WARNING: claude is off (both ANTHROPIC_API_KEY" in output
    assert "CLAUDE_CODE_OAUTH_TOKEN are set" in output
    assert "Generating tasting notes" not in result.stdout
    assert "Enriching menu entries" not in result.stdout
    assert "oauth-canary-9d2c" not in result.stdout + result.stderr
    assert "apikey-canary-4f1a" not in result.stdout + result.stderr
    _assert_site_files(Path(harness.env["SITE_DIR"]))


def test_pipeline_removed_claude_flag_does_not_turn_notes_off(harness):
    path = _stub_claude(harness.root) + os.pathsep + harness.path

    result = harness.run(
        "fetch_docker.sh",
        extra={
            "PATH": path,
            "CLAUDE_CODE_OAUTH_TOKEN": "oauth-canary-9d2c",
            "CLAUDE_ENABLED": "false",
        },
    )

    assert result.returncode == 0, _output(result)
    assert "Generating tasting notes" in result.stdout
    assert "Enriching menu entries" in result.stdout
    assert "Claude feature is off" not in result.stdout


def test_pipeline_claude_gate_off_without_token_even_with_cli(harness):
    path = _stub_claude(harness.root) + os.pathsep + harness.path

    result = harness.run("fetch_docker.sh", extra={"PATH": path})

    assert result.returncode == 0, _output(result)
    assert "Claude feature is off" in result.stdout
    assert "Generating tasting notes" not in result.stdout


# --- Home Assistant REST sensor files ------------------------------------


def _run_script(harness: Harness, script: str, *args: str) -> str:
    result = subprocess.run(
        [sys.executable, f"scripts/{script}", *args],
        cwd=harness.app,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, _output(result)
    return result.stdout


def test_ha_enabled_makes_thisweek_and_today(harness):
    result = harness.run("fetch_docker.sh", extra={"HA_ENABLED": "true"})

    assert result.returncode == 0, _output(result)
    site = Path(harness.env["SITE_DIR"])
    assert "Generating Home Assistant JSON" in result.stdout
    expected_week = _run_script(harness, "thisweek.py", str(site / "plan.json"))
    assert json.loads((site / "thisweek.json").read_text()) == json.loads(expected_week)
    today = json.loads((site / "today.json").read_text())
    assert today["date"] == date.today().isoformat()


def test_readme_sensor_attributes_exist_in_the_json(harness):
    """A renamed field would make a sensor show unknown, with no error."""
    result = harness.run("fetch_docker.sh", extra={"HA_ENABLED": "true"})
    assert result.returncode == 0, _output(result)
    site = Path(harness.env["SITE_DIR"])
    week = json.loads((site / "thisweek.json").read_text())
    # today.json holds one entry of pairing_suggestions.json, or a date and null.
    # pairing.py adds suggested_bottle only when the score is poor or partial, so
    # take the keys of all entries.
    suggestions = json.loads((site / "pairing_suggestions.json").read_text())[
        "suggestions"
    ]
    meal_keys = set().union(*suggestions)

    sensors = ha_rest_sensors()
    assert [Path(s["resource"]).name for s in sensors] == [
        "thisweek.json",
        "today.json",
    ]
    for sensor, keys in zip(sensors, (set(week), meal_keys)):
        spec = sensor["sensor"][0]
        state_field = re.search(r"value_json\.(\w+)", spec["value_template"])
        assert state_field, f"{sensor['resource']}: no field in value_template"
        missing = ({state_field.group(1)} | set(spec["json_attributes"])) - keys
        assert not missing, f"{sensor['resource']}: {sorted(missing)}"


def test_ha_off_by_default_makes_no_files_and_logs_skip(harness):
    result = harness.run("fetch_docker.sh")

    assert result.returncode == 0, _output(result)
    site = Path(harness.env["SITE_DIR"])
    assert not (site / "thisweek.json").exists()
    assert not (site / "today.json").exists()
    assert "Skipping Home Assistant JSON" in result.stdout
    assert "Generating Home Assistant JSON" not in result.stdout


def test_ha_off_removes_files_from_an_earlier_run(harness):
    site = Path(harness.env["SITE_DIR"])
    site.mkdir(parents=True)
    (site / "thisweek.json").write_text("{}")
    (site / "today.json").write_text("{}")

    result = harness.run("fetch_docker.sh", extra={"HA_ENABLED": "false"})

    assert result.returncode == 0, _output(result)
    assert not (site / "thisweek.json").exists()
    assert not (site / "today.json").exists()
    assert "Skipping Home Assistant JSON" in result.stdout
    _assert_site_files(site)


def test_ha_failed_script_removes_its_file_and_run_goes_on(harness):
    site = Path(harness.env["SITE_DIR"])
    site.mkdir(parents=True)
    (site / "thisweek.json").write_text('{"name": "old"}')
    (harness.app / "scripts" / "thisweek.py").write_text(
        "import sys\nprint('{\"na')\nsys.exit(1)\n"
    )

    result = harness.run("fetch_docker.sh", extra={"HA_ENABLED": "true"})

    assert result.returncode == 0, _output(result)
    assert "WARNING: thisweek.py failed (exit 1)" in result.stdout
    # No empty or partial file at the public URL, and no temporary file.
    assert not (site / "thisweek.json").exists()
    assert not list(site.glob(".thisweek.json.*"))
    # The other file and the rest of the run are not affected.
    assert json.loads((site / "today.json").read_text())
    assert "Sync complete" in result.stdout


def test_ha_files_are_readable_by_the_web_server(harness):
    result = harness.run("fetch_docker.sh", extra={"HA_ENABLED": "true"})

    assert result.returncode == 0, _output(result)
    site = Path(harness.env["SITE_DIR"])
    for name in ("thisweek.json", "today.json"):
        assert (site / name).stat().st_mode & 0o777 == 0o644


def test_leftover_ha_temp_files_are_removed_at_start(harness):
    site = Path(harness.env["SITE_DIR"])
    site.mkdir(parents=True)
    (site / ".thisweek.json.AbC123").write_text('{"na')
    (site / ".today.json.XyZ789").write_text("")

    result = harness.run("fetch_docker.sh")

    assert result.returncode == 0, _output(result)
    assert not list(site.glob(".*.json.*"))


# --- Menu calendar is optional ----------------------------------------------


MENU_OFF = ("GOOGLE_CALENDAR_ICS_URL",)


def test_pipeline_without_calendar_url_runs(harness):
    data = Path(harness.env["DATA_DIR"])
    site = Path(harness.env["SITE_DIR"])

    result = harness.run("fetch_docker.sh", drop=MENU_OFF)

    assert result.returncode == 0, _output(result)
    assert (site / "plan.json").is_file()
    assert (site / "report.json").is_file()
    # The four CellarTracker fetches ran. No call went to the calendar.
    urls = harness.curl_urls()
    assert len(urls) == 4, urls
    assert all("cellartracker.com" in u for u in urls), urls
    assert not any("calendar" in u or u.endswith(".ics") for u in urls), urls
    for folder in (data, site):
        assert not (folder / "pairing_suggestions.json").exists()
    assert not (data / "menu.ics").exists()
    assert not (data / "menu.json").exists()
    assert "menu: off (not configured)" in result.stdout
    assert "Skipping menu calendar — menu feature is off" in result.stdout
    assert "Skipping menu enrichment" not in result.stdout
    assert "Generating pairing suggestions" not in result.stdout
    assert "Pairing: menu feature is off" in result.stdout
    # No earlier run published pairings, so nothing is removed.
    assert "removing the pairings of an earlier run" not in result.stdout


def test_pipeline_menu_on_logs_state_and_no_skip_line(harness):
    result = harness.run("fetch_docker.sh")

    assert result.returncode == 0, _output(result)
    assert re.search(r"^\s*menu: on$", result.stdout, re.MULTILINE)
    assert "Skipping menu calendar" not in result.stdout
    assert ICS_URL in harness.curl_urls()


def test_pipeline_menu_off_removes_files_of_an_earlier_run(harness):
    data = Path(harness.env["DATA_DIR"])
    site = Path(harness.env["SITE_DIR"])
    first = harness.run("fetch_docker.sh")
    assert first.returncode == 0, _output(first)
    stale = (
        data / "menu.ics",
        data / "menu.json",
        data / "pairing_suggestions.json",
        site / "pairing_suggestions.json",
    )
    for path in stale:
        assert path.is_file(), path

    second = harness.run("fetch_docker.sh", drop=MENU_OFF)

    assert second.returncode == 0, _output(second)
    for path in stale:
        assert not path.exists(), path
    assert (site / "plan.json").is_file()
    assert (
        "WARNING: menu feature is off — removing the pairings of an earlier run"
        in second.stdout
    )


def test_pipeline_menu_url_with_brackets_is_fetched_as_is(harness):
    # Without -g, curl reads [] as a glob and fails the fetch.
    url = "https://calendar.example.test/private/menu[1].ics"

    result = harness.run("fetch_docker.sh", extra={"GOOGLE_CALENDAR_ICS_URL": url})

    assert result.returncode == 0, _output(result)
    assert url in harness.curl_urls()
    assert (Path(harness.env["SITE_DIR"]) / "pairing_suggestions.json").is_file()


def test_pipeline_menu_on_failed_calendar_fetch_fails_and_pings(harness):
    (harness.fixtures / "menu.ics").unlink()

    result = harness.run("fetch_docker.sh", extra=_enable_healthcheck(harness))

    assert result.returncode != 0, _output(result)
    assert "Google Calendar fetch failed" in result.stdout
    assert ICS_URL in harness.curl_urls()
    assert harness.server.paths == [f"/{KEY_CANARY}/the-sommelier/fail"]


def test_pipeline_menu_off_ignores_a_missing_calendar_fixture(harness):
    # With the menu off, a calendar that would fail is never fetched.
    (harness.fixtures / "menu.ics").unlink()

    result = harness.run(
        "fetch_docker.sh",
        extra=_enable_healthcheck(harness),
        drop=MENU_OFF,
    )

    assert result.returncode == 0, _output(result)
    assert harness.server.paths == [f"/{KEY_CANARY}/the-sommelier"]


def test_pipeline_menu_not_valid_url_is_off_and_not_fetched(harness):
    result = harness.run(
        "fetch_docker.sh", extra={"GOOGLE_CALENDAR_ICS_URL": "ftp://x/SECRET-ics"}
    )

    assert result.returncode == 0, _output(result)
    assert "menu: off (missing or not valid: GOOGLE_CALENDAR_ICS_URL)" in result.stdout
    assert "Skipping menu calendar" in result.stdout
    assert not any("SECRET-ics" in u for u in harness.curl_urls())
    assert "SECRET-ics" not in result.stdout + result.stderr


def test_pipeline_menu_off_with_ha_makes_today_with_no_meal(harness):
    site = Path(harness.env["SITE_DIR"])

    result = harness.run("fetch_docker.sh", extra={"HA_ENABLED": "true"}, drop=MENU_OFF)

    assert result.returncode == 0, _output(result)
    today = json.loads((site / "today.json").read_text())
    assert today == {"date": date.today().isoformat(), "meal": None}
    week = json.loads((site / "thisweek.json").read_text())
    assert week
    assert "WARNING: today.py failed" not in result.stdout


def test_pipeline_menu_off_digest_is_nothing_to_send(harness):
    site = Path(harness.env["SITE_DIR"])
    digest_env = {
        "SMTP_USERNAME": "sender@example.test",
        "SMTP_PASSWORD": "smtp-pw-canary",
        "DIGEST_RECIPIENTS": "a@example.test",
    }

    result = harness.run("fetch_docker.sh", extra=digest_env, drop=MENU_OFF)

    assert result.returncode == 0, _output(result)
    assert "Digest: nothing to send" in result.stdout
    assert "Digest generation failed" not in result.stdout
    assert (site / "digest.json").is_file()
    assert (site / "digest.html").is_file()
