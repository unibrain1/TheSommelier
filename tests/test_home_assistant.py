"""Static checks: Home Assistant pulls the JSON files and no file pushes them."""

import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# A push needs one of these: the old setting, the old key mount, the ssh package
# or an ssh call.
PUSH_MARKERS = re.compile(
    r"HA_SSH_TARGET|ha_ssh_key|openssh|(?:^|[\s;&|(\"'])(?:ssh|scp)(?:\s|$|[\"'])"
)


def _checked_files() -> list[Path]:
    files = sorted(REPO.glob("*.sh"))
    files += sorted(p for p in (REPO / "scripts").glob("*") if p.is_file())
    files += [
        REPO / name
        for name in (
            "Dockerfile",
            "docker-compose.yml",
            "docker-compose.override.yml.sample",
            "examples/compose.yaml",
            ".env.sample",
        )
    ]
    return files


def test_no_file_pushes_to_home_assistant():
    files = _checked_files()
    assert {"pipeline.sh", "Dockerfile", ".env.sample"} <= {f.name for f in files}
    hits = []
    for path in files:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            # settings.py names the old variable to warn a host that still sets it.
            if path.name == "settings.py" and "HA_SSH_TARGET" in line:
                continue
            if PUSH_MARKERS.search(line):
                hits.append(f"{path.relative_to(REPO)}:{number}: {line.strip()}")
    assert hits == []


def test_nginx_serves_json_files_as_json():
    text = (REPO / "nginx.conf").read_text(encoding="utf-8")
    match = re.search(r"location\s+~\*?\s+\\\.json\$\s*\{([^}]*)\}", text)
    assert match, "no .json location in nginx.conf"
    assert "default_type application/json;" in match.group(1)


# --- today.py with no pairing file --------------------------------------


def _run_today(path: Path, menu_url: str | None) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ["PATH"]}
    if menu_url is not None:
        env["GOOGLE_CALENDAR_ICS_URL"] = menu_url
    return subprocess.run(
        [sys.executable, str(REPO / "scripts" / "today.py"), str(path)],
        env=env,
        capture_output=True,
        text=True,
    )


def test_today_missing_file_with_menu_off_gives_no_meal(tmp_path):
    result = _run_today(tmp_path / "pairing_suggestions.json", None)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"date": date.today().isoformat(), "meal": None}


def test_today_missing_file_with_menu_on_fails(tmp_path):
    result = _run_today(
        tmp_path / "pairing_suggestions.json", "https://example.test/menu.ics"
    )

    assert result.returncode != 0
    assert "pairing_suggestions.json" in result.stderr


def test_today_missing_file_with_menu_not_valid_gives_no_meal(tmp_path):
    result = _run_today(tmp_path / "pairing_suggestions.json", "ftp://x/menu.ics")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["meal"] is None


def test_today_existing_file_is_read_with_menu_off(tmp_path):
    path = tmp_path / "pairing_suggestions.json"
    entry = {"date": date.today().isoformat(), "meal": "Dinner"}
    path.write_text(json.dumps({"suggestions": [entry]}))

    result = _run_today(path, None)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == entry
