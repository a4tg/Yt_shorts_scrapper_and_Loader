import ipaddress
import os
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx


DEFAULT_API_URL = "https://api.yookassa.ru/v3"
DEFAULT_WEBHOOK_NETWORKS = (
    "185.71.76.0/27",
    "185.71.77.0/27",
    "77.75.153.0/25",
    "77.75.156.11/32",
    "77.75.156.35/32",
    "77.75.154.128/25",
    "2a02:5180::/32",
)


class YooKassaConfigurationError(RuntimeError):
    pass


class YooKassaAPIError(RuntimeError):
    pass


def env_bool(name: str, default: bool) -> bool:
    fallback = "true" if default else "false"
    return os.getenv(name, fallback).strip().lower() in {"1", "true", "yes", "on"}


def minor_to_value(amount_minor: int) -> str:
    if amount_minor < 0:
        raise ValueError("Payment amount cannot be negative")
    return f"{Decimal(amount_minor) / Decimal(100):.2f}"


def value_to_minor(value: object) -> int:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Invalid provider amount") from exc
    minor = decimal_value * 100
    if minor != minor.to_integral_value():
        raise ValueError("Provider amount has sub-kopeck precision")
    return int(minor)


def webhook_ip_allowed(address: str | None) -> bool:
    if not address:
        return False
    if not env_bool("YOOKASSA_WEBHOOK_ENFORCE_IP", True):
        return True
    configured = os.getenv("YOOKASSA_WEBHOOK_NETWORKS", "").strip()
    networks = tuple(item.strip() for item in configured.split(",") if item.strip())
    if not networks:
        networks = DEFAULT_WEBHOOK_NETWORKS
    try:
        source = ipaddress.ip_address(address)
        return any(source in ipaddress.ip_network(network, strict=False) for network in networks)
    except ValueError:
        return False


class YooKassaClient:
    def __init__(self) -> None:
        self.shop_id = os.getenv("YOOKASSA_SHOP_ID", "").strip()
        self.secret_key = os.getenv("YOOKASSA_SECRET_KEY", "").strip()
        self.api_url = os.getenv("YOOKASSA_API_URL", DEFAULT_API_URL).rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.shop_id and self.secret_key)

    def _require_configuration(self) -> None:
        if not self.configured:
            raise YooKassaConfigurationError(
                "ЮKassa не настроена: нужны YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY."
            )

    def _request(
        self,
        method: str,
        path: str,
        *,
        idempotency_key: str | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_configuration()
        headers = {"Accept": "application/json"}
        if idempotency_key:
            headers["Idempotence-Key"] = idempotency_key
        try:
            with httpx.Client(
                auth=(self.shop_id, self.secret_key),
                timeout=httpx.Timeout(20.0, connect=5.0),
                follow_redirects=False,
            ) as client:
                response = client.request(
                    method,
                    f"{self.api_url}{path}",
                    headers=headers,
                    json=json_body,
                )
        except httpx.HTTPError as exc:
            raise YooKassaAPIError("ЮKassa временно недоступна.") from exc
        if response.status_code >= 400:
            request_id = response.headers.get("X-Request-Id", "unknown")
            raise YooKassaAPIError(
                f"ЮKassa отклонила запрос (HTTP {response.status_code}, request {request_id})."
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise YooKassaAPIError("ЮKassa вернула некорректный JSON.") from exc
        if not isinstance(payload, dict):
            raise YooKassaAPIError("ЮKassa вернула неожиданный ответ.")
        return payload

    def create_payment(
        self,
        *,
        amount_minor: int,
        currency: str,
        description: str,
        idempotency_key: str,
        metadata: dict[str, str],
        return_url: str | None = None,
        payment_method_id: str | None = None,
        customer_email: str | None = None,
        save_payment_method: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "amount": {"value": minor_to_value(amount_minor), "currency": currency},
            "capture": True,
            "description": description[:128],
            "metadata": metadata,
        }
        if payment_method_id:
            body["payment_method_id"] = payment_method_id
        else:
            if not return_url:
                raise ValueError("return_url is required for an initial payment")
            body["confirmation"] = {"type": "redirect", "return_url": return_url}
            body["save_payment_method"] = save_payment_method
        if env_bool("YOOKASSA_RECEIPT_ENABLED", False):
            if not customer_email:
                raise ValueError("customer_email is required when receipt is enabled")
            vat_code = max(1, min(int(os.getenv("YOOKASSA_VAT_CODE", "1")), 12))
            body["receipt"] = {
                "customer": {"email": customer_email},
                "items": [
                    {
                        "description": description[:128],
                        "quantity": "1.00",
                        "amount": body["amount"],
                        "vat_code": vat_code,
                        "payment_mode": "full_payment",
                        "payment_subject": "service",
                    }
                ],
            }
        return self._request(
            "POST",
            "/payments",
            idempotency_key=idempotency_key,
            json_body=body,
        )

    def get_payment(self, provider_payment_id: str) -> dict[str, Any]:
        if not provider_payment_id or len(provider_payment_id) > 160:
            raise ValueError("Invalid provider payment id")
        return self._request("GET", f"/payments/{provider_payment_id}")
