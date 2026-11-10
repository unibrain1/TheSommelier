"""Tests for send_digest.py — email building and idempotency."""

import json
import smtplib
import ssl

import pytest

import send_digest
import settings
from send_digest import already_sent_today, build_email, mark_sent

SETTING_VARS = settings.ALL_VARS


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in SETTING_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class TestBuildEmail:
    def test_subject_uses_tonight_suggestion_when_available(self):
        digest = {
            "wine": {"vintage": "2020", "name": "Dion Vineyard Pinot Noir"},
            "tonight": {
                "bottle": {"vintage": "2010", "wine": "Vin Santo"},
                "meal": "Pasta",
                "pairing": {"score": "partial"},
            },
        }
        msg = build_email(digest, "<html>body</html>", "from@test.com", ["to@test.com"])
        assert "Vin Santo" in msg["Subject"]
        assert "pull" in msg["Subject"]

    def test_subject_falls_back_to_weekly_wine(self):
        digest = {
            "wine": {"vintage": "2020", "name": "Dion Vineyard Pinot Noir"},
        }
        msg = build_email(digest, "<html>body</html>", "from@test.com", ["to@test.com"])
        assert "Dion Vineyard Pinot Noir" in msg["Subject"]
        assert "2020" in msg["Subject"]

    def test_subject_fallback_when_no_wine(self):
        msg = build_email(
            {"wine": None}, "<html>body</html>", "from@test.com", ["to@test.com"]
        )
        assert "The Sommelier" in msg["Subject"]

    def test_recipients_in_header(self):
        msg = build_email(
            {"wine": None},
            "<html></html>",
            "from@test.com",
            ["a@test.com", "b@test.com"],
        )
        assert "a@test.com" in msg["To"]
        assert "b@test.com" in msg["To"]

    def test_html_content_type(self):
        msg = build_email(
            {"wine": None}, "<html>test</html>", "from@test.com", ["to@test.com"]
        )
        payload = msg.get_payload()
        assert len(payload) == 1
        assert payload[0].get_content_type() == "text/html"


class TestIdempotency:
    def test_not_sent_when_no_state_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        assert already_sent_today() is False

    def test_sent_today_returns_true(self, tmp_path, monkeypatch):
        monkeypatch.setattr("send_digest._today_local", lambda: "2026-04-18")
        (tmp_path / "digest_last_sent.txt").write_text("2026-04-18")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        assert already_sent_today() is True

    def test_sent_yesterday_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr("send_digest._today_local", lambda: "2026-04-18")
        (tmp_path / "digest_last_sent.txt").write_text("2026-04-17")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        assert already_sent_today() is False

    def test_mark_sent_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("send_digest._today_local", lambda: "2026-04-18")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        mark_sent()
        state = tmp_path / "digest_last_sent.txt"
        assert state.exists()
        assert state.read_text() == "2026-04-18"

    def test_mark_sent_creates_missing_data_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("send_digest._today_local", lambda: "2026-04-18")
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "new" / "dir"))
        mark_sent()
        assert (
            tmp_path / "new" / "dir" / "digest_last_sent.txt"
        ).read_text() == "2026-04-18"


class RecordingSMTP:
    """Replaces smtplib.SMTP and records the calls."""

    instances: list["RecordingSMTP"] = []
    refuse: dict[str, tuple] = {}

    def __init__(self, host, port):
        self.host, self.port = host, port
        self.calls: list[tuple] = []
        RecordingSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.tls_context = context
        self.calls.append(("starttls",))

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg):
        self.calls.append(("send_message", msg))
        return dict(RecordingSMTP.refuse)


@pytest.fixture
def smtp(monkeypatch):
    RecordingSMTP.instances = []
    RecordingSMTP.refuse = {}
    monkeypatch.setattr(smtplib, "SMTP", RecordingSMTP)
    return RecordingSMTP


CREDS = {
    "SMTP_USERNAME": "sender@example.com",
    "SMTP_PASSWORD": "SECRET-canary-smtp-pass",
    "DIGEST_RECIPIENTS": "a@example.com,b@example.com",
}


def set_creds(monkeypatch, **extra):
    for key, value in {**CREDS, **extra}.items():
        monkeypatch.setenv(key, value)


class TestSendEmail:
    def test_send_email_uses_default_host_port(self, monkeypatch, smtp):
        set_creds(monkeypatch)
        msg = build_email(
            {"wine": None}, "<p>x</p>", "sender@example.com", ["a@example.com"]
        )
        send_digest.send_email(msg, settings.smtp())
        (server,) = smtp.instances
        assert (server.host, server.port) == ("smtp.gmail.com", 587)
        assert server.calls == [
            ("starttls",),
            ("login", "sender@example.com", "SECRET-canary-smtp-pass"),
            ("send_message", msg),
        ]

    def test_send_email_uses_env_host_port(self, monkeypatch, smtp):
        set_creds(monkeypatch, SMTP_HOST="mail.example.test", SMTP_PORT="2525")
        msg = build_email(
            {"wine": None}, "<p>x</p>", "sender@example.com", ["a@example.com"]
        )
        send_digest.send_email(msg, settings.smtp())
        (server,) = smtp.instances
        assert (server.host, server.port) == ("mail.example.test", 2525)
        assert [c[0] for c in server.calls] == ["starttls", "login", "send_message"]

    def test_send_email_refuses_a_port_that_is_not_valid(self, monkeypatch, smtp):
        set_creds(monkeypatch, SMTP_PORT="abc")
        msg = build_email(
            {"wine": None}, "<p>x</p>", "sender@example.com", ["a@example.com"]
        )
        with pytest.raises(ValueError, match="SMTP_PORT"):
            send_digest.send_email(msg, settings.smtp())
        assert smtp.instances == []

    def test_send_email_checks_server_certificate(self, monkeypatch, smtp):
        # Without a context, smtplib does not check the certificate, so a
        # man in the middle on SMTP_HOST could read the password at login.
        set_creds(monkeypatch)
        msg = build_email(
            {"wine": None}, "<p>x</p>", "sender@example.com", ["a@example.com"]
        )
        send_digest.send_email(msg, settings.smtp())
        (server,) = smtp.instances
        assert server.tls_context is not None
        assert server.tls_context.verify_mode == ssl.CERT_REQUIRED
        assert server.tls_context.check_hostname is True


@pytest.fixture
def digest_dirs(tmp_path, monkeypatch):
    """A digest for 'today' in SITE_DIR, and an empty DATA_DIR."""
    data, site = tmp_path / "data", tmp_path / "site"
    data.mkdir()
    site.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SITE_DIR", str(site))
    monkeypatch.setattr("send_digest._today_local", lambda: "2026-04-18")
    digest = {"date": "2026-04-18", "has_content": True, "wine": None, "tonight": None}
    (site / "digest.json").write_text(json.dumps(digest), encoding="utf-8")
    (site / "digest.html").write_text("<html>body</html>", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["send_digest.py"])
    return data, site


class TestMain:
    def test_main_sends_and_marks_sent_in_data_dir(
        self, digest_dirs, monkeypatch, smtp, capsys
    ):
        data, _ = digest_dirs
        set_creds(monkeypatch)
        send_digest.main()
        (server,) = smtp.instances
        assert [c[0] for c in server.calls] == ["starttls", "login", "send_message"]
        assert (data / "digest_last_sent.txt").read_text() == "2026-04-18"
        assert "Digest sent to a@example.com, b@example.com" in capsys.readouterr().out
        # Second run the same day does not send again.
        send_digest.main()
        assert len(smtp.instances) == 1

    def test_main_smtp_error_prints_class_and_code_only(
        self, digest_dirs, monkeypatch, smtp, capsys
    ):
        set_creds(monkeypatch)

        def fail_login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"SECRET-canary-server-text")

        monkeypatch.setattr(RecordingSMTP, "login", fail_login)
        with pytest.raises(SystemExit) as exc:
            send_digest.main()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "ERROR: Failed to send digest email (SMTPAuthenticationError 535)" in err
        assert "SECRET-canary" not in err
        assert not (digest_dirs[0] / "digest_last_sent.txt").exists()

    def test_main_stale_digest_exits_3_with_error(
        self, digest_dirs, monkeypatch, smtp, capsys
    ):
        set_creds(monkeypatch)
        monkeypatch.setattr("send_digest._today_local", lambda: "2026-04-19")
        with pytest.raises(SystemExit) as exc:
            send_digest.main()
        assert exc.value.code == 3
        err = capsys.readouterr().err
        assert (
            "ERROR: Digest is for 2026-04-18, not today (2026-04-19). "
            "Use --force to override." in err
        )
        assert smtp.instances == []
        assert not (digest_dirs[0] / "digest_last_sent.txt").exists()

    def test_main_missing_credentials_exits_1(
        self, digest_dirs, monkeypatch, smtp, capsys
    ):
        monkeypatch.setenv("SMTP_USERNAME", "SECRET-canary-user-only")
        with pytest.raises(SystemExit) as exc:
            send_digest.main()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "SMTP_PASSWORD" in err
        assert "DIGEST_RECIPIENTS" in err
        assert "SECRET-canary" not in err
        assert smtp.instances == []
        assert not (digest_dirs[0] / "digest_last_sent.txt").exists()

    def test_main_nothing_configured_exits_0_with_info_line(
        self, digest_dirs, smtp, capsys
    ):
        # No exit: the scheduled job must not fail when the digest is not set up.
        send_digest.main()
        captured = capsys.readouterr()
        assert "Digest email not configured" in captured.out
        assert "nothing to send" in captured.out
        assert captured.err == ""
        assert smtp.instances == []
        assert not (digest_dirs[0] / "digest_last_sent.txt").exists()

    def test_main_nothing_configured_and_no_digest_files_exits_0(
        self, tmp_path, monkeypatch, smtp, capsys
    ):
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("SITE_DIR", str(tmp_path / "empty-site"))
        monkeypatch.setattr("sys.argv", ["send_digest.py"])
        send_digest.main()
        assert "not configured" in capsys.readouterr().out

    @pytest.mark.parametrize(
        ("present", "missing"),
        [
            ("SMTP_USERNAME", ["SMTP_PASSWORD", "DIGEST_RECIPIENTS"]),
            ("SMTP_PASSWORD", ["SMTP_USERNAME", "DIGEST_RECIPIENTS"]),
            ("DIGEST_RECIPIENTS", ["SMTP_USERNAME", "SMTP_PASSWORD"]),
        ],
    )
    def test_main_partial_settings_exit_1_and_name_missing(
        self, digest_dirs, monkeypatch, smtp, capsys, present, missing
    ):
        monkeypatch.setenv(present, "SECRET-canary-partial")
        with pytest.raises(SystemExit) as exc:
            send_digest.main()
        assert exc.value.code == 1
        captured = capsys.readouterr()
        for name in missing:
            assert name in captured.err
        assert "SECRET-canary" not in captured.out + captured.err
        assert smtp.instances == []

    def test_main_dry_run_needs_no_credentials(
        self, digest_dirs, monkeypatch, smtp, capsys
    ):
        monkeypatch.setattr("sys.argv", ["send_digest.py", "--dry-run"])
        send_digest.main()
        out = capsys.readouterr().out
        assert "=== DRY RUN ===" in out
        assert "Subject: The Sommelier" in out
        assert smtp.instances == []
        assert not (digest_dirs[0] / "digest_last_sent.txt").exists()

    @pytest.mark.parametrize("flag", ["false", "off"])
    def test_main_removed_flag_does_not_stop_the_email(
        self, digest_dirs, monkeypatch, smtp, capsys, flag
    ):
        set_creds(monkeypatch, DIGEST_ENABLED=flag)
        send_digest.main()
        captured = capsys.readouterr()
        assert len(smtp.instances) == 1
        assert "Digest sent to" in captured.out
        assert "Digest off" not in captured.out + captured.err

    def test_main_partly_refused_warns_and_marks_sent(
        self, digest_dirs, monkeypatch, smtp, capsys
    ):
        data, _ = digest_dirs
        set_creds(monkeypatch)
        smtp.refuse = {"b@example.com": (550, b"no such user")}
        send_digest.main()
        out, err = capsys.readouterr()
        assert "Digest sent to a@example.com\n" in out
        assert "WARNING: The server refused: b@example.com" in err
        assert (data / "digest_last_sent.txt").read_text() == "2026-04-18"

    def test_main_refused_named_recipient_is_not_listed_as_sent(
        self, digest_dirs, monkeypatch, smtp, capsys
    ):
        set_creds(monkeypatch, DIGEST_RECIPIENTS="a@example.com,Bee <b@example.com>")
        smtp.refuse = {"b@example.com": (550, b"no such user")}
        send_digest.main()
        out = capsys.readouterr().out
        assert "Digest sent to a@example.com\n" in out

    def test_main_op_recipient_in_list_exits_1(
        self, digest_dirs, monkeypatch, smtp, capsys
    ):
        set_creds(monkeypatch, DIGEST_RECIPIENTS="a@example.com,op://v/r")
        with pytest.raises(SystemExit) as exc:
            send_digest.main()
        assert exc.value.code == 1
        assert "DIGEST_RECIPIENTS" in capsys.readouterr().err
        assert smtp.instances == []

    def test_main_invalid_smtp_port_exits_1(
        self, digest_dirs, monkeypatch, smtp, capsys
    ):
        set_creds(monkeypatch, SMTP_PORT="abc")
        with pytest.raises(SystemExit) as exc:
            send_digest.main()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "SMTP_PORT" in err
        assert "SECRET-canary" not in err
        assert smtp.instances == []

    def test_from_header_defaults_to_username(self, digest_dirs, monkeypatch, smtp):
        set_creds(monkeypatch)
        send_digest.main()
        msg = smtp.instances[0].calls[-1][1]
        assert msg["From"] == "sender@example.com"
        assert msg["To"] == "a@example.com, b@example.com"

    def test_from_header_uses_smtp_from(self, digest_dirs, monkeypatch, smtp):
        set_creds(monkeypatch, SMTP_FROM="Sommelier <wine@example.com>")
        send_digest.main()
        msg = smtp.instances[0].calls[-1][1]
        assert msg["From"] == "Sommelier <wine@example.com>"
        # Login still uses the account, not the From address.
        assert smtp.instances[0].calls[1][1] == "sender@example.com"

    def test_main_smtp_failure_exits_1_and_does_not_mark_sent(
        self, digest_dirs, monkeypatch, capsys
    ):
        class Failing(RecordingSMTP):
            def login(self, user, password):
                raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

        monkeypatch.setattr(smtplib, "SMTP", Failing)
        set_creds(monkeypatch)
        with pytest.raises(SystemExit) as exc:
            send_digest.main()
        assert exc.value.code == 1
        assert not (digest_dirs[0] / "digest_last_sent.txt").exists()
