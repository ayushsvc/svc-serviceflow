import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import get_settings


def send_email(message: EmailMessage) -> None:
    settings = get_settings()
    if settings.smtp_use_ssl:
        with smtplib.SMTP_SSL(
            settings.smtp_host,
            settings.smtp_port,
            timeout=10,
            context=ssl.create_default_context(),
        ) as smtp:
            _send(smtp, message, settings.smtp_username, settings.smtp_password)
        return

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        if settings.smtp_starttls:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        _send(smtp, message, settings.smtp_username, settings.smtp_password)


def _send(smtp, message: EmailMessage, username: str | None, password) -> None:
    if username and password:
        smtp.login(username, password.get_secret_value())
    smtp.send_message(message)
