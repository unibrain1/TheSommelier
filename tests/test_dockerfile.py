"""Static checks of the Dockerfile, the shell scripts and .dockerignore."""

import re
from pathlib import Path, PurePosixPath

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = (
    "entrypoint.sh",
    "pipeline.sh",
    "fetch.sh",
    "fetch_docker.sh",
    "send_digest.sh",
)


def _dockerfile_lines() -> list[str]:
    """Return the instructions with line continuations joined and comments dropped."""
    text = (REPO / "Dockerfile").read_text(encoding="utf-8")
    text = re.sub(r"\\\n", " ", text)
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _instructions(name: str) -> list[str]:
    return [
        line.split(None, 1)[1]
        for line in _dockerfile_lines()
        if line.split()[0] == name
    ]


def _code_lines(script: str) -> list[str]:
    lines = (REPO / script).read_text(encoding="utf-8").splitlines()
    return [line for line in lines if not line.lstrip().startswith("#")]


def test_dockerfile_copies_the_code_into_app():
    assert ". /app" in _instructions("COPY")


def test_dockerfile_installs_no_git():
    apt_lines = [line for line in _instructions("RUN") if "apt-get install" in line]

    assert apt_lines
    for line in apt_lines:
        assert not re.search(r"\bgit\b", line), line


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_runs_no_git_and_no_pip_install(script):
    code = _code_lines(script)
    assert code

    for line in code:
        assert not re.search(r"\bgit\b", line), f"{script}: {line}"
        assert "pip install" not in line, f"{script}: {line}"


@pytest.mark.parametrize(
    ("getter", "var"),
    [("sync_schedule", "SYNC_SCHEDULE"), ("digest_schedule", "DIGEST_SEND_SCHEDULE")],
)
def test_entrypoint_reads_schedules_from_settings(getter, var):
    # settings.py stops a value with a newline. A direct env read would put
    # that value into the crontab, where each line is a command.
    code = "\n".join(_code_lines("entrypoint.sh"))
    assert re.search(
        rf"=\$\(python3 /app/scripts/settings\.py get {getter}\) \|\| (exit 1|\{{)",
        code,
    )
    assert not re.search(rf"\$\{{?{var}\b", code)


def test_dockerfile_user_is_numeric_and_not_root():
    users = _instructions("USER")

    assert len(users) == 1
    uid = users[0].split(":")[0]
    assert uid.isdigit(), users[0]
    assert int(uid) != 0


def test_dockerfile_healthcheck_calls_container_health():
    health = _instructions("HEALTHCHECK")

    assert len(health) == 1
    assert "container_health.py" in health[0]


# --- .dockerignore ---------------------------------------------------------


def _pattern_regex(pattern: str) -> re.Pattern[str]:
    """Translate a .dockerignore pattern: `*` stays in one folder, `**` crosses them."""
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out + "$")


def _ignored(path: str) -> bool:
    """Apply the rules in order. The last rule that matches the path or a parent wins."""
    parts = PurePosixPath(path).parts
    prefixes = ["/".join(parts[: n + 1]) for n in range(len(parts))]
    ignored = False
    for raw in (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines():
        rule = raw.strip()
        if not rule or rule.startswith("#"):
            continue
        negated = rule.startswith("!")
        regex = _pattern_regex(rule.lstrip("!").strip("/"))
        if any(regex.match(prefix) for prefix in prefixes):
            ignored = not negated
    return ignored


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "config/plan.yaml",
        "config/holidays.yaml",
        "config/evolution_tracks.yaml",
        "data/inventory.json",
        "plans/issues/issue-112.md",
        ".git/HEAD",
        "docker-compose.override.yml",
        "site/plan.json",
        "site/thisweek.json",
        "site/digest.html",
        "scripts/plan_config.py",
    ],
)
def test_dockerignore_excludes(path):
    assert _ignored(path), f"{path} would go into the image"


@pytest.mark.parametrize(
    "path",
    [
        "scripts/settings.py",
        "scripts/container_health.py",
        "entrypoint.sh",
        "fetch_docker.sh",
        "requirements.txt",
        "nginx.conf",
        "site/index.html",
    ],
)
def test_dockerignore_keeps_what_the_container_runs(path):
    assert (REPO / path).exists(), path
    assert not _ignored(path), f"{path} would be missing from the image"


# --- Supply chain ----------------------------------------------------------


def test_dockerfile_pins_base_image_by_digest():
    froms = _instructions("FROM")

    assert froms
    for line in froms:
        # The tag stays next to the digest, so Dependabot can update both.
        assert re.match(r"python:3\.14-slim@sha256:[0-9a-f]{64}\b", line), line


@pytest.mark.parametrize("arch", ("amd64", "arm64"))
def test_dockerfile_checks_supercronic_sha256(arch):
    args = dict(line.split("=", 1) for line in _instructions("ARG") if "=" in line)
    sha = args.get(f"SUPERCRONIC_SHA256_{arch}", "")
    assert re.fullmatch(r"[0-9a-f]{64}", sha), sha

    runs = [line for line in _instructions("RUN") if "supercronic-linux-" in line]
    assert len(runs) == 1
    run = runs[0]
    assert f"${{SUPERCRONIC_SHA256_{arch}}}" in run
    assert "sha256sum -c -" in run
    assert "--connect-timeout" in run and "--max-time" in run
