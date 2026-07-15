import os
from unittest.mock import MagicMock, patch

import pytest

from email_service import EmailConfigurationError, account_link, send_account_email


def test_account_token_stays_in_url_fragment() -> None:
    with patch.dict(os.environ, {"YT_LOADER_PUBLIC_BASE_URL": "https://shorts.example.test"}):
        link = account_link("reset", "secret-token")
    assert link == "https://shorts.example.test/#reset=secret-token"
    assert "?" not in link


def test_account_links_reject_insecure_public_url() -> None:
    with patch.dict(os.environ, {"YT_LOADER_PUBLIC_BASE_URL": "http://shorts.example.test"}):
        with pytest.raises(EmailConfigurationError):
            account_link("verify", "secret-token")


def test_smtp_uses_starttls_and_optional_authentication() -> None:
    smtp = MagicMock()
    connection = MagicMock()
    connection.__enter__.return_value = smtp
    with patch.dict(
        os.environ,
        {
            "SMTP_HOST": "smtp.example.test",
            "SMTP_PORT": "587",
            "SMTP_SECURITY": "starttls",
            "SMTP_USERNAME": "mailer",
            "SMTP_PASSWORD": "secret",
            "SMTP_FROM_EMAIL": "no-reply@example.test",
        },
    ), patch("email_service.smtplib.SMTP", return_value=connection) as constructor:
        send_account_email("person@example.test", "Subject", "Body")
    constructor.assert_called_once_with("smtp.example.test", 587, timeout=10)
    smtp.starttls.assert_called_once()
    smtp.login.assert_called_once_with("mailer", "secret")
    sent = smtp.send_message.call_args.args[0]
    assert sent["To"] == "person@example.test"
    assert sent.get_content().strip() == "Body"
