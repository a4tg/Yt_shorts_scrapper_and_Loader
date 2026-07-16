import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlparse


logger = logging.getLogger(__name__)


class EmailConfigurationError(RuntimeError):
    pass


def _truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def email_verification_required() -> bool:
    return _truthy("YT_LOADER_REQUIRE_EMAIL_VERIFICATION")


def smtp_configured() -> bool:
    return bool(os.getenv("SMTP_HOST", "").strip() and os.getenv("SMTP_FROM_EMAIL", "").strip())


def email_features_configured() -> bool:
    if not smtp_configured():
        return False
    try:
        public_base_url()
    except EmailConfigurationError:
        return False
    return True


def public_base_url() -> str:
    value = os.getenv("YT_LOADER_PUBLIC_BASE_URL", "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise EmailConfigurationError("Для писем нужен публичный HTTPS-адрес сервиса")
    return value


def account_link(action: str, raw_token: str) -> str:
    if action not in {"verify", "reset"}:
        raise ValueError("Unsupported account link action")
    # The fragment is not sent to the web server or written to access logs.
    return f"{public_base_url()}/#{action}={raw_token}"


def _smtp_port() -> int:
    try:
        return max(1, min(int(os.getenv("SMTP_PORT", "587")), 65535))
    except ValueError as exc:
        raise EmailConfigurationError("Некорректный SMTP_PORT") from exc


def send_account_email(recipient: str, subject: str, body: str) -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    sender = os.getenv("SMTP_FROM_EMAIL", "").strip()
    if not host or not sender or "\n" in sender or "\r" in sender:
        raise EmailConfigurationError("SMTP не настроен")
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    mode = os.getenv("SMTP_SECURITY", "starttls").strip().lower()
    if mode not in {"starttls", "ssl", "none"}:
        raise EmailConfigurationError("SMTP_SECURITY должен быть starttls, ssl или none")

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject.replace("\r", " ").replace("\n", " ")[:200]
    message.set_content(body)
    context = ssl.create_default_context()
    connection = (
        smtplib.SMTP_SSL(host, _smtp_port(), timeout=10, context=context)
        if mode == "ssl"
        else smtplib.SMTP(host, _smtp_port(), timeout=10)
    )
    with connection as smtp:
        if mode == "starttls":
            smtp.starttls(context=context)
        if username:
            smtp.login(username, password)
        smtp.send_message(message)


def send_verification_email(recipient: str, raw_token: str) -> None:
    link = account_link("verify", raw_token)
    send_account_email(
        recipient,
        "Подтвердите email в All As Planned",
        "Подтвердите адрес электронной почты, открыв ссылку:\n\n"
        f"{link}\n\nСсылка действует 24 часа. Если вы не регистрировались, проигнорируйте письмо.",
    )


def send_password_reset_email(recipient: str, raw_token: str) -> None:
    link = account_link("reset", raw_token)
    send_account_email(
        recipient,
        "Восстановление пароля в All As Planned",
        "Для установки нового пароля откройте ссылку:\n\n"
        f"{link}\n\nСсылка действует 1 час и может быть использована один раз. "
        "Если вы не запрашивали восстановление, ничего не делайте.",
    )
