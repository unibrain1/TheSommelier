"""Static checks: compose and nginx agree on the folder that holds the site."""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SERVICES = ("the-sommelier", "run-now")


def _service_env(service: str) -> dict[str, str]:
    """Read the `environment:` list of one service in docker-compose.yml.

    PyYAML is not a dependency of the project, so this reads the one shape that
    the file uses: a list of `- NAME=value` lines under `environment:`.
    """
    lines = (REPO / "docker-compose.yml").read_text(encoding="utf-8").splitlines()
    start = lines.index(f"  {service}:")
    env: dict[str, str] = {}
    in_env = False
    for line in lines[start + 1 :]:
        if re.match(r"  \S", line):  # the next service
            break
        if re.match(r"    environment:\s*$", line):
            in_env = True
            continue
        if in_env:
            match = re.match(r"      - ([A-Z_]+)=(.*?)\s*$", line)
            if match:
                env[match.group(1)] = match.group(2)
            elif not re.match(r"\s*(#.*)?$", line):
                break
    return env


def _nginx_root() -> str:
    match = re.search(r"^\s*root\s+(\S+?);", (REPO / "nginx.conf").read_text(), re.M)
    assert match, "no root in nginx.conf"
    return match.group(1)


@pytest.mark.parametrize("service", SERVICES)
def test_compose_service_sets_data_dir_and_site_dir(service):
    env = _service_env(service)

    assert env.get("DATA_DIR"), f"{service} sets no DATA_DIR: {env}"
    assert env.get("SITE_DIR"), f"{service} sets no SITE_DIR: {env}"


@pytest.mark.parametrize("service", SERVICES)
def test_compose_site_folder_is_the_nginx_root(service):
    env = _service_env(service)
    site = env.get("SITE_DIR") or f"{env['DATA_DIR']}/site"

    assert site == _nginx_root()
