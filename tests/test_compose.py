"""Static checks of docker-compose.yml and nginx.conf for the standalone image."""

import re
from pathlib import Path

import pytest
import yaml

import settings

REPO = Path(__file__).resolve().parent.parent
SERVICES = ("the-sommelier", "run-now")
NGINX_TEMP_DIRECTIVES = (
    "client_body_temp_path",
    "proxy_temp_path",
    "fastcgi_temp_path",
    "uwsgi_temp_path",
    "scgi_temp_path",
)


COMPOSE_FILES = ("docker-compose.yml", "examples/compose.yaml")


@pytest.fixture(scope="module", params=COMPOSE_FILES)
def compose(request) -> dict:
    """Each check runs on the build file and on the sample for the published image."""
    return yaml.safe_load((REPO / request.param).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def sample() -> dict:
    return yaml.safe_load((REPO / "examples/compose.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def nginx_conf() -> str:
    """The nginx config without comments, so a comment cannot satisfy a check."""
    text = (REPO / "nginx.conf").read_text(encoding="utf-8")
    return re.sub(r"#.*", "", text)


def _directive_values(conf: str, name: str) -> list[str]:
    return re.findall(rf"^\s*{name}\s+(\S+?);", conf, re.M)


def _root_in_block(conf: str, opener: str) -> str:
    """Return the `root` of the first block that starts with `opener`."""
    start = conf.index(opener)
    match = re.search(r"\broot\s+(\S+?);", conf[start:])
    assert match, f"no root after {opener!r}"
    return match.group(1)


@pytest.mark.parametrize("service", SERVICES)
def test_compose_has_no_bind_mount_of_the_repo(compose, service):
    volumes = compose["services"][service]["volumes"]
    assert volumes, f"{service} has no volumes"

    for volume in volumes:
        if isinstance(volume, str):
            target = volume.split(":")[1]
        else:
            target = volume["target"]
        # The code comes from the image. Any mount at /app brings back host code.
        assert target != "/app" and not target.startswith("/app/"), (
            f"{service} mounts over the code: {volume}"
        )


@pytest.mark.parametrize("service", SERVICES)
def test_compose_sets_no_path_environment(compose, service):
    env = compose["services"][service]["environment"]
    assert isinstance(env, dict)

    for name in ("DATA_DIR", "SITE_DIR", "PLAN_CONFIG"):
        assert name not in env, f"{service} sets {name}"


@pytest.mark.parametrize("service", SERVICES)
def test_compose_sets_the_pacific_time_zone(compose, service):
    # A service-level `environment:` replaces the anchor block, so a service
    # that sets its own `environment:` must repeat TZ. Without it, supercronic
    # reads the schedule in UTC.
    env = compose["services"][service]["environment"]

    assert env["TZ"] == "America/Los_Angeles"


@pytest.mark.parametrize("service", SERVICES)
def test_compose_data_is_the_named_volume(compose, service):
    volumes = compose["services"][service]["volumes"]

    assert "sommelier-data:/data" in volumes
    assert "sommelier-data" in compose["volumes"]


def test_compose_run_now_runs_one_sync(compose):
    run_now = compose["services"]["run-now"]

    assert run_now["command"][-1] == "/app/fetch_docker.sh"
    assert run_now["entrypoint"] == []
    assert run_now["profiles"] == ["run-now"]
    assert run_now["healthcheck"] == {"disable": True}


def test_compose_scheduled_service_is_not_in_the_run_now_profile(compose):
    assert "profiles" not in compose["services"]["the-sommelier"]


@pytest.mark.parametrize("service", SERVICES)
def test_compose_user_is_not_root(compose, service):
    user = str(compose["services"][service]["user"])

    uid = user.split(":")[0]
    assert uid.isdigit(), f"user {user!r} is not numeric"
    assert int(uid) != 0


@pytest.mark.parametrize("service", SERVICES)
def test_compose_service_is_hardened(compose, service):
    config = compose["services"][service]

    assert config["read_only"] is True
    assert "/tmp" in config["tmpfs"]
    assert config["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in config["security_opt"]


@pytest.mark.parametrize("service", SERVICES)
def test_sample_uses_the_published_image_and_does_not_build(sample, service):
    config = sample["services"][service]

    # A version tag, not latest or edge: the sample must not follow a moving tag.
    assert re.fullmatch(
        r"ghcr\.io/unibrain1/thesommelier:\d+\.\d+(\.\d+)?", config["image"]
    )
    assert "build" not in config


@pytest.mark.parametrize("service", SERVICES)
def test_sample_stops_when_a_required_secret_is_missing(sample, service):
    env = sample["services"][service]["environment"]

    for name in settings.REQUIRED:
        assert env[name].startswith("${" + name + ":?"), f"{service} {name}"


def test_sample_follows_compose_naming_practice(sample):
    # A fixed project name keeps the volume name stable. A fixed container
    # name blocks a second project and a scale-out, so the sample sets none.
    assert sample["name"] == "the-sommelier"
    assert "version" not in sample
    for service, config in sample["services"].items():
        assert "container_name" not in config, service
        options = config["logging"]["options"]
        assert options["max-size"] and options["max-file"], service


def test_sample_restarts_the_site_service_only(sample):
    assert sample["services"]["the-sommelier"]["restart"] in (
        "unless-stopped",
        "always",
    )
    assert "restart" not in sample["services"]["run-now"]


@pytest.mark.parametrize(
    "name",
    [
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "CT_COMMUNITY_NOTES_RSS",
        "HEALTHCHECK_URL",
    ],
)
def test_sample_reads_each_optional_secret_from_the_host(name):
    # The docs say that a ${NAME} line is a secret that goes in .env. A literal
    # value here would lead the user to paste the secret into compose.yaml.
    text = (REPO / "examples" / "compose.yaml").read_text(encoding="utf-8")

    assert re.search(rf'^\s*#?\s?{name}: "\${{{name}}}"$', text, re.M)


def test_nginx_static_root_is_the_image_site(nginx_conf):
    assert _root_in_block(nginx_conf, "server {") == "/app/site"


def test_nginx_data_root_is_the_default_site_dir(nginx_conf, monkeypatch):
    for name in ("DATA_DIR", "SITE_DIR"):
        monkeypatch.delenv(name, raising=False)

    assert _root_in_block(nginx_conf, "location @data") == str(settings.site_dir())


def test_nginx_writes_only_to_tmp(nginx_conf):
    pids = _directive_values(nginx_conf, "pid")
    temp_paths = [
        path
        for name in NGINX_TEMP_DIRECTIVES
        for path in _directive_values(nginx_conf, name)
    ]

    assert len(pids) == 1
    assert len(temp_paths) == len(NGINX_TEMP_DIRECTIVES)
    for path in pids + temp_paths:
        assert path.startswith("/tmp/"), path


def test_nginx_has_no_user_line(nginx_conf):
    assert not re.search(r"^\s*user\s", nginx_conf, re.M)
