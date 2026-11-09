"""Static checks: the docs and the samples match the env variables the code reads."""

import re
import subprocess
from pathlib import Path

import pytest
import yaml

import settings

REPO = Path(__file__).resolve().parent.parent

# settings.py names these only to warn a host that still sets them.
REMOVED_VARS = {"HA_SSH_TARGET", settings.UNUSED_OP_VAR, *settings.REMOVED_FLAGS}
# settings.py names these to pass them to the claude CLI. They are not settings
# of this app, so the docs and the samples do not list them.
CLAUDE_ENV_PASS = {*settings.CLAUDE_ENV_NAMES, *settings.CLAUDE_ENV_PREFIXES}
# HEALTHCHECK_URL replaced these. settings.py ignores them and does not name them.
OLD_HEALTHCHECK_VARS = {
    "HEALTHCHECK_PING_KEY",
    "HEALTHCHECK_SLUG",
    "HEALTHCHECK_BASE_URL",
    "HEALTHCHECK_ENABLED",
}
# The removed variables that a v3 release read. The others came and went
# inside the v4.0.0 milestone, so no v3 .env holds them.
V3_VARS = {"HA_SSH_TARGET", "HEALTHCHECK_PING_KEY", settings.UNUSED_OP_VAR}
# Scripts read these outside settings.py, which does not own them.
SHELL_VARS = {"TZ"}
UPPER_NAME = re.compile(r"""["']([A-Z][A-Z0-9_]{2,})["']""")

SAMPLES = (
    ".env.sample",
    "examples/compose.yaml",
    "docker-compose.override.yml.sample",
    "config/holidays.yaml.sample",
    "config/evolution_tracks.yaml.sample",
)
STALE = re.compile(
    "|".join([r"\bgit\s+pull\b", r"\bop\s+read\b"])
    + "".join(rf"|\b{name}\b" for name in sorted(REMOVED_VARS | OLD_HEALTHCHECK_VARS)),
    re.I,
)
# The code reads these, but a user never sets them. The docs and the samples
# must not list them: the image fixes the folders. ANTHROPIC_AUTH_TOKEN is
# for gateways and is not a Claude choice. settings.py names it only to warn.
INTERNAL_VARS = {
    "DATA_DIR",
    "SITE_DIR",
    "CONFIG_DIR",
    "ANTHROPIC_AUTH_TOKEN",
}
CLAUDE_KEYS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
ONE_KEY_COMMENT = "set one of the two keys, or neither"
UPGRADE_PAGE = "docs/upgrade-from-v3.md"


def _current_docs() -> list[str]:
    """Every tracked doc and sample that describes how things work now."""
    names = ["README.md", "CLAUDE.md", "docker-compose.yml", "Dockerfile", *SAMPLES]
    names += [p.relative_to(REPO).as_posix() for p in (REPO / "docs").glob("*.md")]
    # Only tracked files count, so a clean clone and the owner's machine agree.
    tracked = subprocess.run(
        ["git", "ls-files", "--", ".claude/*.md"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    names += tracked
    # A worktree is a copy of the repo. The deploy skill is local to the owner,
    # is not in git, and describes the old production host until #116.
    skip = (".claude/worktrees/", ".claude/skills/deploy/")
    return sorted(n for n in set(names) if not n.startswith(skip))


def _text(name: str) -> str:
    return (REPO / name).read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """Return the text from `heading` to the next heading of the same or a higher level."""
    depth = len(heading.split(" ", 1)[0])
    start = text.index(f"\n{heading}\n")
    rest = text[start + 1 :]
    offset, fenced = len(heading), False
    for line in rest[len(heading) :].splitlines(keepends=True):
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and re.match(rf"#{{1,{depth}}} ", line):
            return rest[:offset]
        offset += len(line)
    return rest


def _code_env_names() -> set[str]:
    names = set(UPPER_NAME.findall(_text("scripts/settings.py")))
    return (names - REMOVED_VARS - CLAUDE_ENV_PASS) | SHELL_VARS


def _user_settings() -> set[str]:
    """Return the variables that the docs and the samples must list."""
    return _code_env_names() - INTERNAL_VARS


# Names that a shell script reads but that are not settings.
SHELL_BUILTINS = {"PWD"}


def _shell_inputs(text: str) -> set[str]:
    """Return the variables that a shell script reads and does not set itself."""
    used = set(re.findall(r"\$\{?([A-Z][A-Z0-9_]+)", text))
    assigned = set(
        re.findall(
            r"(?:^|[\s;{(])(?:export\s+|local\s+)?([A-Z][A-Z0-9_]+)=", text, re.M
        )
    )
    assigned |= set(re.findall(r"read\s+(?:-\w+\s+)*([A-Z][A-Z0-9_]+)", text))
    assigned |= set(re.findall(r"for\s+([A-Z][A-Z0-9_]+)\s+in", text))
    # NAME="${NAME:-x}" sets a default for a variable that the host may set.
    defaulted = set(re.findall(r"\b([A-Z][A-Z0-9_]+)=\"?\$\{?\1\b", text))
    return used - (assigned - defaulted) - SHELL_BUILTINS


def _python_inputs(text: str) -> set[str]:
    """Return the variables that Python code reads from the environment."""
    quoted = r"""["']([A-Z][A-Z0-9_]+)["']"""
    names = set(
        re.findall(
            rf"(?:environ(?:\.get|\.setdefault|\.pop)?[\[(]|getenv\()\s*{quoted}", text
        )
    )
    return names | set(re.findall(rf"{quoted} in os\.environ", text))


@pytest.mark.parametrize(
    ("reader", "line", "expected"),
    [
        (_shell_inputs, 'echo "${NEW_KNOB}"', {"NEW_KNOB"}),
        (_shell_inputs, 'X="${NEW_KNOB:-x}"', {"NEW_KNOB"}),
        (_shell_inputs, 'SET_HERE=1\necho "$SET_HERE"', set()),
        (_shell_inputs, 'KNOB="${KNOB:-x}"\necho "$KNOB"', {"KNOB"}),
        (_shell_inputs, 'RC=$?; echo "${RC}"', set()),
        (_python_inputs, "os.environ.get('NEW_KNOB')", {"NEW_KNOB"}),
        (_python_inputs, 'os.environ.setdefault("NEW_KNOB", "1")', {"NEW_KNOB"}),
        (_python_inputs, 'os.getenv("NEW_KNOB")', {"NEW_KNOB"}),
        (_python_inputs, 'os.environ.pop("NEW_KNOB", None)', {"NEW_KNOB"}),
        (_python_inputs, '"NEW_KNOB" in os.environ', {"NEW_KNOB"}),
        (_python_inputs, 'os.environ["NEW_KNOB"]', {"NEW_KNOB"}),
    ],
)
def test_the_env_readers_find_each_form(reader, line, expected):
    assert reader(line) == expected


def test_no_script_reads_an_env_variable_that_the_test_does_not_collect():
    shell, python = set(), set()
    for path in REPO.glob("*.sh"):
        shell |= _shell_inputs(path.read_text("utf-8"))
    for path in (REPO / "scripts").glob("*.py"):
        if path.name != "settings.py":
            python |= _python_inputs(path.read_text("utf-8"))

    assert shell, "no shell variables found"
    assert SHELL_VARS <= shell | python, "a script no longer reads a SHELL_VARS name"
    assert sorted(shell - _code_env_names()) == []
    assert sorted(python - _code_env_names()) == []


def test_the_code_reads_the_variables_this_test_expects():
    names = _code_env_names()

    # A broken regex must not pass with an empty or small list.
    assert len(names) >= 23
    assert {"USERNAME", "CLAUDE_CODE_OAUTH_TOKEN", "HA_ENABLED", "TZ"} <= names
    assert INTERNAL_VARS <= names, "an internal variable is gone: update INTERNAL_VARS"
    assert not names & REMOVED_VARS


def test_every_env_variable_is_a_row_of_the_configuration_table():
    section = _section(_text("docs/configuration.md"), "### Environment variables")
    rows = set(re.findall(r"^\| `([A-Z][A-Z0-9_]+)` \|", section, re.M))

    assert sorted(_user_settings() - rows) == []
    # A renamed or removed variable must leave the table too.
    assert sorted(rows - _code_env_names()) == []
    # An internal variable is not a user setting.
    assert sorted(rows & INTERNAL_VARS) == []


def test_the_configuration_table_marks_only_the_required_variables_as_required():
    section = _section(_text("docs/configuration.md"), "### Environment variables")
    rows = re.findall(r"^\| `([A-Z][A-Z0-9_]+)` \|[^|]*\| (Yes|No) \|", section, re.M)
    required = {name for name, flag in rows if flag == "Yes"}
    optional = {name for name, flag in rows if flag == "No"}

    assert required == set(settings.REQUIRED)
    assert "GOOGLE_CALENDAR_ICS_URL" in optional


@pytest.mark.parametrize("sample", (".env.sample", "examples/compose.yaml"))
def test_every_env_variable_is_in_the_sample(sample):
    text = _text(sample)

    missing = [n for n in sorted(_user_settings()) if not re.search(rf"\b{n}\b", text)]
    assert missing == []


def _setting_names(sample: str) -> set[str]:
    """Return the names on setting lines, commented or not, not names in prose."""
    text = _text(sample)
    if sample == ".env.sample":
        return set(re.findall(r"^#?\s?([A-Z][A-Z0-9_]+)=", text, re.M))
    block = text.split("  environment:\n", 1)[1].split("\n  volumes:", 1)[0]
    return set(re.findall(r"^\s*#?\s?([A-Z][A-Z0-9_]+):", block, re.M))


@pytest.mark.parametrize("sample", (".env.sample", "examples/compose.yaml"))
def test_the_sample_has_one_setting_line_for_each_env_variable(sample):
    # A prose mention does not count: the user uncomments the setting line.
    assert sorted(_setting_names(sample) ^ _user_settings()) == []


def test_the_stale_reference_scan_covers_the_expected_files():
    names = _current_docs()

    assert len(names) >= 12
    assert {"README.md", ".claude/agents/pipeline-reviewer.md", "Dockerfile"} <= set(
        names
    )


@pytest.mark.parametrize("name", _current_docs())
def test_no_current_doc_has_a_stale_reference(name):
    text = _text(name)
    if name == UPGRADE_PAGE:
        # Only the upgrade page may tell a v3 user to remove the old variables.
        for old in V3_VARS:
            text = re.sub(rf"\b{old}\b", "", text, flags=re.I)

    assert STALE.findall(text) == []


def test_the_upgrade_page_still_names_the_removed_variable():
    # A v3 user must see which old variable to remove. Delete this test only
    # with the SSH text.
    assert "HA_SSH_TARGET" in _text(UPGRADE_PAGE)


@pytest.mark.parametrize("old", sorted(V3_VARS))
def test_the_upgrade_page_names_each_removed_variable(old):
    # The release notes link to the upgrade page for these names.
    assert re.search(rf"\b{old}\b", _text(UPGRADE_PAGE)), old


def test_the_docker_quick_start_needs_no_checkout():
    section = _section(_text("README.md"), "### Run with Docker")

    base = "https://raw.githubusercontent.com/unibrain1/TheSommelier/main/"
    assert base + "examples/compose.yaml" in section
    assert base + "config/holidays.yaml.sample" in section
    assert base + "config/evolution_tracks.yaml.sample" in section
    assert "plan.yaml.sample" not in section
    assert "docker compose up -d" in section
    for forbidden in ("git clone", "cp .env.sample", "docker compose build"):
        assert forbidden not in section


def test_the_quick_start_steps_match_the_sample():
    section = _section(_text("README.md"), "### Run with Docker")
    sample = yaml.safe_load(_text("examples/compose.yaml"))

    assert "curl -fsSLo compose.yaml " in section
    assert "curl -fsSLo config/holidays.yaml " in section
    assert "curl -fsSLo config/evolution_tracks.yaml " in section
    assert "config/plan.yaml" not in section
    for service in sample["services"].values():
        assert "./config:/config:ro" in service["volumes"]
    ports = sample["services"]["the-sommelier"]["ports"]
    host_port = ports[0].rsplit(":", 2)[-2]
    assert f"http://localhost:{host_port}" in section
    # The .env example holds each required variable, or compose stops.
    for name in settings.REQUIRED:
        assert re.search(rf"^\s*{name}=", section, re.M), name


@pytest.mark.parametrize(
    "name",
    ["README.md", "CLAUDE.md", "docs/plan-configuration.md", "examples/compose.yaml"],
)
def test_docs_describe_both_files(name):
    text = _text(name)
    assert "holidays.yaml" in text
    assert "evolution_tracks.yaml" in text


# These name the old plan.yaml on purpose: the upgrade page, the release notes
# and the one old-file warning (docs/plan-configuration.md). The
# private_paths line of .claude/project.md keeps it, because the owner's old
# file stays on disk.
OLD_FILE_ALLOWED = {
    UPGRADE_PAGE,
    "docs/plan-configuration.md",
    ".claude/project.md",
}


@pytest.mark.parametrize(
    "name",
    [
        n
        for n in _current_docs()
        if n not in OLD_FILE_ALLOWED and not n.startswith("docs/releases/")
    ],
)
def test_current_docs_do_not_name_plan_yaml(name):
    assert not re.search(r"\bplan\.yaml\b", _text(name)), name


def test_the_old_file_allow_list_is_needed():
    # A name on the list that no longer names plan.yaml should leave the list.
    for name in OLD_FILE_ALLOWED - {UPGRADE_PAGE}:
        assert re.search(r"\bplan\.yaml\b", _text(name)), name


def test_claude_md_has_no_ghcr_private_note():
    assert "stays private" not in _text("CLAUDE.md")


def test_features_gives_the_logo_rule():
    section = _section(_text("docs/features.md"), "## Morning digest email")
    for part in (
        "`site/logo.png`",
        "from the code",
        "`SITE_URL` + `/logo.png`",
        "`https://` URL",
        "warning",
    ):
        assert part in section, part


def test_the_old_file_warning_names_the_edge_builds():
    text = _text("docs/plan-configuration.md")
    assert "v4.0.0 pre-release (edge) builds used a `plan.yaml` file" in text
    assert not re.search(r"\bv3\b", text)


def test_the_quick_start_says_how_to_update_the_image():
    assert "docker compose pull" in _section(_text("README.md"), "## Quick Start")


def _anchors(text: str) -> set[str]:
    """Return the GitHub anchors of the headings, not counting fenced code."""
    anchors: set[str] = set()
    seen: dict[str, int] = {}
    in_fence = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        match = None if in_fence else re.match(r"#{1,6}\s+(.*)", line)
        if match:
            slug = re.sub(r"[^\w\- ]", "", match.group(1).strip().lower())
            slug = slug.replace(" ", "-")
            count = seen.get(slug, 0)
            anchors.add(slug if count == 0 else f"{slug}-{count}")
            seen[slug] = count + 1
    return anchors


@pytest.mark.parametrize(
    "name",
    [
        "README.md",
        "CLAUDE.md",
        *sorted(p.relative_to(REPO).as_posix() for p in (REPO / "docs").glob("*.md")),
    ],
)
def test_every_relative_link_and_anchor_resolves(name):
    path = REPO / name
    text = path.read_text("utf-8")
    prose = re.sub(r"```.*?```", "", text, flags=re.S)
    links = re.findall(r"\]\(([^)\s]+)\)", prose) + re.findall(r'src="([^"]+)"', prose)

    bad = []
    for link in links:
        if re.match(r"[a-z][a-z0-9+.-]*:", link):
            continue
        target, _, anchor = link.partition("#")
        file = (path.parent / target).resolve() if target else path
        if not file.exists():
            bad.append(f"missing {link}")
        elif (
            anchor
            and file.suffix == ".md"
            and anchor not in _anchors(file.read_text("utf-8"))
        ):
            bad.append(f"bad anchor {link}")
    assert bad == []


def test_the_link_checker_finds_a_broken_link(tmp_path):
    page = tmp_path / "a.md"
    page.write_text("# Title\n\n## Same\n\n## Same\n", encoding="utf-8")

    assert {"title", "same", "same-1"} <= _anchors(page.read_text("utf-8"))
    assert "nope" not in _anchors(page.read_text("utf-8"))


def test_the_user_docs_and_samples_do_not_name_internal_variables():
    names = ["README.md", *SAMPLES]
    names += [p.relative_to(REPO).as_posix() for p in (REPO / "docs").glob("*.md")]
    # docker-compose.yml is not a user doc: it sets the folders for the build.
    found = {
        (name, var)
        for name in names
        for var in INTERNAL_VARS
        if re.search(rf"\b{var}\b", _text(name))
    }

    assert sorted(found) == []


def test_the_claude_choice_table_has_the_three_choices():
    section = _section(
        _text("docs/features.md"), "## Claude tasting notes and menu enrichment"
    )
    rows = {
        cells[0]: cells[1:]
        for cells in (
            [c.strip() for c in line.strip("|").split("|")]
            for line in section.splitlines()
            if line.startswith("|")
        )
    }
    token, api_key = (f"`{name}`" for name in CLAUDE_KEYS)

    assert "Set at most one key." in section
    assert rows["No Claude"][0] == "none"
    assert token in rows["No Claude"][1] and api_key in rows["No Claude"][1]
    assert rows["Subscription"] == [token, api_key]
    assert rows["API"] == [api_key, token]


@pytest.mark.parametrize("sample", (".env.sample", "examples/compose.yaml"))
def test_the_sample_says_to_set_one_claude_key_or_neither(sample):
    # The comment must sit next to the two key lines, not elsewhere in the file.
    lines = _text(sample).splitlines()
    comment = next(i for i, line in enumerate(lines) if ONE_KEY_COMMENT in line.lower())
    nearby = "\n".join(lines[comment : comment + 8])

    for name in CLAUDE_KEYS:
        assert re.search(rf"^\s*#?\s?{name}[=:]", nearby, re.M), name


@pytest.mark.parametrize("name", ["holidays.yaml", "evolution_tracks.yaml"])
def test_each_sample_names_its_own_target_file(name):
    text = _text(f"config/{name}.sample")
    base = "https://raw.githubusercontent.com/unibrain1/TheSommelier/main/"
    assert f"-fsSLo config/{name} {base}config/{name}.sample" in text
