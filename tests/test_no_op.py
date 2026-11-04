"""Static checks: no script calls the 1Password CLI, and the image has no op."""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
# Any `op <subcommand>` call, and a check that the CLI exists.
OP_CALL = re.compile(r"\bop [A-Za-z-]|\bcommand -v op\b")

# settings.py names `op run` in text only: the module docstring and the warning
# that tells the user how to resolve an op:// value. Neither calls op.
TEXT_ONLY_MENTIONS = {
    ("scripts/settings.py", "(compose env_file, op run, Ansible vault)"),
    ("scripts/settings.py", "for example with op run."),
}


def _scanned_files() -> list[Path]:
    files = sorted(REPO.glob("*.sh"))
    files += sorted(p for p in (REPO / "scripts").glob("*") if p.is_file())
    return files


def test_no_script_calls_op():
    files = _scanned_files()
    assert files, "no files scanned"
    names = {f.name for f in files}
    assert {"pipeline.sh", "entrypoint.sh", "send_digest.py"} <= names

    hits = []
    for path in files:
        rel = path.relative_to(REPO).as_posix()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not OP_CALL.search(line):
                continue
            if any(rel == f and snippet in line for f, snippet in TEXT_ONLY_MENTIONS):
                continue
            hits.append(f"{rel}:{number}: {line.strip()}")
    assert hits == []


def test_text_only_mentions_still_exist():
    """An allow-list entry that matches nothing is stale. Remove it."""
    for rel, snippet in TEXT_ONLY_MENTIONS:
        assert snippet in (REPO / rel).read_text(encoding="utf-8")


def test_env_sample_has_no_op_reference_as_value():
    lines = (REPO / ".env.sample").read_text(encoding="utf-8").splitlines()
    assignments = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    # Only the required values are set lines. The optional feature values,
    # such as the menu calendar URL, are comments.
    names = {ln.split("=", 1)[0].strip() for ln in assignments}
    assert names == {"USERNAME", "PASSWORD"}
    assert [ln for ln in assignments if "op://" in ln] == []
    # A commented example line is also not allowed to hold a reference value.
    assert [ln for ln in lines if re.match(r"#\s*[A-Z_]+=op://", ln)] == []


def test_dockerfile_has_no_op_or_unzip():
    text = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM" in text
    for forbidden in ("op_linux", "agilebits", "unzip"):
        assert forbidden not in text
    assert not OP_CALL.search(text)


@pytest.mark.parametrize(
    "line",
    [
        "op read op://v/i/f",
        "x=$(op read foo)",
        "op run -- bash",
        "op daemon",
        "op item get x",
        "op whoami",
        "if command -v op >/dev/null; then",
    ],
)
def test_op_pattern_catches_calls(line):
    assert OP_CALL.search(line)


@pytest.mark.parametrize(
    "line", ["USERNAME=op://v/i/f", "stop the loop", "# top of file", "shop run"]
)
def test_op_pattern_ignores_other_text(line):
    assert not OP_CALL.search(line)
