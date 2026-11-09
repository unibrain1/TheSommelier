"""Opt-in tests that build and run the real image.

Run with SOMMELIER_DOCKER_TESTS=1. The build takes minutes, so the normal test
command skips this file. Each run uses its own image tag, container names and
volume name, and removes them at the end.
"""

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
# A fake login makes the settings check pass. --network none makes the fetch
# fail at once, so no test reaches CellarTracker.
FAKE_ENV = {
    "USERNAME": "test-user",
    "PASSWORD": "test-password",
    "GOOGLE_CALENDAR_ICS_URL": "http://127.0.0.1:9/menu.ics",
}
HARDENING = [
    "--read-only",
    "--tmpfs",
    "/tmp",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges:true",
]


def _docker(
    *args: str, check: bool = True, timeout: int = 900
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=check, timeout=timeout
    )


def _docker_available() -> bool:
    if os.environ.get("SOMMELIER_DOCKER_TESTS") != "1":
        return False
    try:
        return _docker("info", check=False, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(
    not _docker_available(), reason="set SOMMELIER_DOCKER_TESTS=1 and start Docker"
)


class Sandbox:
    """The names of one test run, and the cleanup of everything it creates."""

    def __init__(self) -> None:
        suffix = uuid.uuid4().hex[:10]
        self.image = f"sommelier-test:{suffix}"
        self.volume = f"sommelier-test-{suffix}"
        self.prefix = f"sommelier-test-{suffix}"
        self.containers: list[str] = []

    def name(self, label: str) -> str:
        name = f"{self.prefix}-{label}"
        self.containers.append(name)
        return name

    def start(self, label: str, *extra: str) -> str:
        """Start the full container in the background and return its name."""
        name = self.name(label)
        env = [arg for k, v in FAKE_ENV.items() for arg in ("-e", f"{k}={v}")]
        _docker(
            "run", "-d", "--name", name, "--network", "none", "-v", f"{self.volume}:/data",
            *HARDENING, *env, *extra, self.image,
        )  # fmt: skip
        return name

    def wait_ready(self, name: str) -> str:
        """Wait for the entrypoint to print Ready. Return the container log."""
        log = ""
        for _ in range(60):
            result = _docker("logs", name, check=False)
            log = result.stdout + result.stderr
            if "==> Ready." in log:
                return log
            state = _docker("inspect", "-f", "{{.State.Running}}", name, check=False)
            assert state.stdout.strip() == "true", f"container stopped:\n{log}"
            time.sleep(1)
        pytest.fail(f"container never became ready:\n{log}")

    def remove_container(self, name: str) -> None:
        _docker("rm", "-f", name, check=False)

    def cleanup(self) -> None:
        for name in self.containers:
            self.remove_container(name)
        _docker("volume", "rm", "-f", self.volume, check=False)
        _docker("image", "rm", "-f", self.image, check=False)


@pytest.fixture(scope="module")
def sandbox():
    box = Sandbox()
    try:
        _docker("build", "-t", box.image, str(REPO), timeout=1800)
        yield box
    finally:
        box.cleanup()


@pytest.fixture(scope="module")
def running(sandbox):
    """One container with the hardening and no bind mount, ready to serve."""
    name = sandbox.start("main")
    log = sandbox.wait_ready(name)
    return name, log


def _exec(name: str, *cmd: str) -> subprocess.CompletedProcess:
    return _docker("exec", name, *cmd, check=False)


def test_image_runs_with_no_bind_mount(running):
    name, _ = running

    mounts = _docker(
        "inspect", "-f", "{{range .Mounts}}{{.Type}} {{end}}", name
    ).stdout.split()

    assert mounts == ["volume"]
    assert _exec(name, "test", "-f", "/app/entrypoint.sh").returncode == 0


def test_image_has_no_git(running):
    name, _ = running

    result = _exec(name, "bash", "-c", "command -v git")

    assert result.returncode != 0
    assert result.stdout == ""


def test_container_user_is_not_root(running):
    name, _ = running

    result = _exec(name, "id", "-u")

    assert result.returncode == 0
    assert result.stdout.strip() not in ("", "0")


def test_container_reports_a_health_state(running):
    name, _ = running

    status = _docker("inspect", "-f", "{{.State.Health.Status}}", name).stdout.strip()

    assert status in ("starting", "healthy", "unhealthy")


def test_hardened_container_starts_and_nginx_has_no_alerts(sandbox, running):
    name, log = running

    host_config = _docker(
        "inspect",
        "-f",
        "{{.HostConfig.ReadonlyRootfs}} {{.HostConfig.CapDrop}} {{.HostConfig.SecurityOpt}}",
        name,
    ).stdout
    assert "true" in host_config
    assert "ALL" in host_config
    assert "no-new-privileges" in host_config
    assert (
        _exec(
            name, "curl", "-sf", "-o", "/dev/null", "http://127.0.0.1:8080/index.html"
        ).returncode
        == 0
    )
    # nginx writes to stderr, which the container log holds.
    assert "[alert]" not in log
    assert "[emerg]" not in log


def test_site_serves_index_from_image_and_plan_from_volume(sandbox, running):
    name, _ = running
    _exec(name, "bash", "-c", "echo '{\"seeded\": true}' > /data/site/plan.json")

    index = _exec(name, "curl", "-sf", "http://127.0.0.1:8080/index.html")
    root = _exec(name, "curl", "-sf", "http://127.0.0.1:8080/")
    plan = _exec(name, "curl", "-sf", "http://127.0.0.1:8080/plan.json")

    assert index.returncode == 0
    assert "<html" in index.stdout.lower()
    # Traefik and users open the root URL, not /index.html.
    assert root.returncode == 0
    assert root.stdout == index.stdout
    assert plan.returncode == 0
    assert plan.stdout.strip() == '{"seeded": true}'


def test_volume_survives_removing_the_container(sandbox):
    box = sandbox
    marker = f"kept-{uuid.uuid4().hex}"
    first = box.name("first")
    _docker(
        "run", "--name", first, "-v", f"{box.volume}:/data", *HARDENING,
        "--entrypoint", "sh", box.image, "-c", f"echo {marker} > /data/state.txt",
    )  # fmt: skip
    box.remove_container(first)

    second = _docker(
        "run", "--rm", "-v", f"{box.volume}:/data", *HARDENING,
        "--entrypoint", "cat", box.image, "/data/state.txt",
    )  # fmt: skip

    assert second.stdout.strip() == marker


def test_run_now_checks_settings_and_stops_at_the_fetch(sandbox):
    env = [arg for k, v in FAKE_ENV.items() for arg in ("-e", f"{k}={v}")]

    result = _docker(
        "run", "--rm", "--network", "none", "-v", f"{sandbox.volume}:/data", *HARDENING, *env,
        "--entrypoint", "bash", sandbox.image, "/app/fetch_docker.sh", check=False,
    )  # fmt: skip

    output = result.stdout + result.stderr
    assert "Checking settings" in output
    assert result.returncode != 0
    assert "CellarTracker inventory fetch failed" in output


def test_image_holds_no_secret_or_personal_file(sandbox, tmp_path):
    # Build from a copy of the repo that holds a canary for each file that
    # .dockerignore must keep out. The real working tree may not hold them.
    # Only tracked files, so a private file of the developer never reaches the
    # temp folder and the context does not change with the local tree.
    context = tmp_path / "context"
    tracked = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")
    for rel in filter(None, tracked):
        if (REPO / rel).is_file():
            (context / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / rel, context / rel)
    canaries = [
        ".env",
        "config/plan.yaml",
        "config/holidays.yaml",
        "config/evolution_tracks.yaml",
        "scripts/plan_config.py",
        "site/plan.json",
        "site/report.json",
        "site/digest.html",
        "data/state.txt",
        "plans/canary.md",
        ".git/HEAD",
    ]
    for rel in canaries:
        path = context / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("canary\n")
    image = f"{sandbox.image}-canary"
    try:
        _docker("build", "-t", image, str(context), timeout=1800)

        found = [
            rel
            for rel in canaries
            if _docker(
                "run", "--rm", "--entrypoint", "test", image, "-e", f"/app/{rel}",
                check=False,
            ).returncode == 0
        ]  # fmt: skip
    finally:
        _docker("image", "rm", "-f", image, check=False)

    assert found == []


def test_entrypoint_refuses_a_site_dir_other_than_data_site(sandbox):
    result = _docker(
        "run", "--name", sandbox.name("guard"), "-v", f"{sandbox.volume}:/data", *HARDENING,
        "-e", "SITE_DIR=/tmp/site", sandbox.image, check=False, timeout=60,
    )  # fmt: skip

    assert result.returncode != 0
    assert "must be /data/site" in result.stderr


@pytest.mark.parametrize("var", ["SYNC_SCHEDULE", "DIGEST_SEND_SCHEDULE"])
def test_container_stops_on_schedule_with_newline(sandbox, var):
    # The second line would be a crontab entry that runs a command.
    env = [arg for k, v in FAKE_ENV.items() for arg in ("-e", f"{k}={v}")]

    result = _docker(
        "run", "--rm", "--network", "none", "-v", f"{sandbox.volume}:/data", *HARDENING, *env,
        "-e", f"{var}=0 1 * * *\n* * * * * touch /tmp/injected", sandbox.image,
        check=False, timeout=120,
    )  # fmt: skip

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert f"ERROR: {var} holds a control character" in output
    assert "==> Ready." not in output
    assert "touch /tmp/injected" not in output


def test_notes_generate_in_the_image(sandbox):
    """The allow list must leave the CLI all it needs. Needs a real token."""
    token = os.environ.get("SOMMELIER_CLAUDE_TEST_TOKEN", "")
    if not token:
        pytest.skip("set SOMMELIER_CLAUDE_TEST_TOKEN to a Claude subscription token")
    # PASSWORD is set as in production, so the CLI runs with the allow list
    # applied. The unit tests check that it does not pass. The reply proves that
    # the CLI ran.
    code = (
        "import sys; sys.path.insert(0, '/app/scripts'); import wine_utils; "
        "reply = wine_utils.call_claude('Reply with the word OK and nothing else.'); "
        "print(reply); sys.exit(0 if reply else 1)"
    )

    result = _docker(
        "run", "--rm", *HARDENING, "-e", f"CLAUDE_CODE_OAUTH_TOKEN={token}",
        "-e", "PASSWORD=canary-password-143", "--entrypoint", "python3", sandbox.image,
        "-c", code, check=False, timeout=300,
    )  # fmt: skip

    assert result.returncode == 0, result.stderr[-500:]
    assert "OK" in result.stdout.upper()
