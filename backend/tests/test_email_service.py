from email.message import EmailMessage
from types import SimpleNamespace

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.services import email as email_service


class FakeSMTP:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def ehlo(self):
        self.calls.append(("ehlo",))

    def starttls(self, **kwargs):
        self.calls.append(("starttls", kwargs))

    def login(self, username, password):
        self.calls.append(("login", username, password))

    def send_message(self, message):
        self.calls.append(("send", message["To"]))


def test_send_email_uses_starttls_and_authentication(monkeypatch):
    smtp = FakeSMTP()
    monkeypatch.setattr(
        email_service,
        "get_settings",
        lambda: SimpleNamespace(
            smtp_use_ssl=False,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_starttls=True,
            smtp_username="svc-user",
            smtp_password=SecretStr("svc-password"),
        ),
    )
    monkeypatch.setattr(email_service.smtplib, "SMTP", lambda *_args, **_kwargs: smtp)
    monkeypatch.setattr(email_service.ssl, "create_default_context", lambda: "tls-context")
    message = EmailMessage()
    message["To"] = "user@example.com"

    email_service.send_email(message)

    assert [call[0] for call in smtp.calls] == ["ehlo", "starttls", "ehlo", "login", "send"]
    assert smtp.calls[3] == ("login", "svc-user", "svc-password")
    assert smtp.calls[4] == ("send", "user@example.com")


def test_authenticated_smtp_requires_a_tls_mode():
    with pytest.raises(ValidationError, match="Authenticated SMTP requires STARTTLS or SSL"):
        Settings(
            jwt_secret="test-secret",
            debug=True,
            smtp_username="svc-user",
            smtp_password="svc-password",
        )
