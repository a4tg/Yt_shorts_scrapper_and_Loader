import os
from unittest.mock import patch

import pytest

from payment_service import PaymentNotConfiguredError, PaymentValidationError, public_base_url, validate_confirmation_url
from yookassa_client import YooKassaClient, minor_to_value, value_to_minor


class CapturingClient(YooKassaClient):
    def __init__(self) -> None:
        super().__init__()
        self.request = None

    def _request(self, method, path, *, idempotency_key=None, json_body=None):
        self.request = {
            "method": method,
            "path": path,
            "idempotency_key": idempotency_key,
            "json": json_body,
        }
        return {"id": "test-payment", "status": "pending"}


def test_amount_conversion_is_exact() -> None:
    assert minor_to_value(49900) == "499.00"
    assert value_to_minor("499.00") == 49900
    with pytest.raises(ValueError):
        value_to_minor("1.001")


def test_initial_payment_contains_idempotency_metadata_and_optional_receipt() -> None:
    with patch.dict(
        os.environ,
        {
            "YOOKASSA_SHOP_ID": "test-shop",
            "YOOKASSA_SECRET_KEY": "test-secret",
            "YOOKASSA_RECEIPT_ENABLED": "true",
            "YOOKASSA_VAT_CODE": "12",
        },
    ):
        client = CapturingClient()
        client.create_payment(
            amount_minor=49900,
            currency="RUB",
            description="YT Loader Creator",
            idempotency_key="local-idempotency-key",
            metadata={"local_payment_id": "local-1", "user_id": "user-1", "plan_id": "creator"},
            return_url="https://shorts.example.test/?payment=local-1",
            customer_email="payer@example.com",
            save_payment_method=True,
        )
    body = client.request["json"]
    assert client.request["idempotency_key"] == "local-idempotency-key"
    assert body["capture"] is True
    assert body["amount"] == {"value": "499.00", "currency": "RUB"}
    assert body["confirmation"]["type"] == "redirect"
    assert body["save_payment_method"] is True
    assert body["receipt"]["customer"]["email"] == "payer@example.com"
    assert body["receipt"]["items"][0]["vat_code"] == 12
    assert "test-secret" not in str(body)


def test_refund_uses_payment_amount_and_idempotency_key() -> None:
    client = CapturingClient()

    client.create_refund(
        provider_payment_id="provider-payment",
        amount_minor=149000,
        currency="RUB",
        description="Полный возврат",
        idempotency_key="refund-idempotency",
    )

    assert client.request["method"] == "POST"
    assert client.request["path"] == "/refunds"
    assert client.request["idempotency_key"] == "refund-idempotency"
    assert client.request["json"]["payment_id"] == "provider-payment"
    assert client.request["json"]["amount"] == {
        "value": "1490.00",
        "currency": "RUB",
    }


def test_recurring_payment_uses_saved_method_without_confirmation() -> None:
    with patch.dict(
        os.environ,
        {
            "YOOKASSA_SHOP_ID": "test-shop",
            "YOOKASSA_SECRET_KEY": "test-secret",
            "YOOKASSA_RECEIPT_ENABLED": "false",
        },
    ):
        client = CapturingClient()
        client.create_payment(
            amount_minor=49900,
            currency="RUB",
            description="Renewal",
            idempotency_key="renewal-key",
            metadata={"local_payment_id": "local-2", "user_id": "user-1", "plan_id": "creator"},
            payment_method_id="saved-method",
            customer_email="payer@example.com",
        )
    body = client.request["json"]
    assert body["payment_method_id"] == "saved-method"
    assert "confirmation" not in body
    assert "save_payment_method" not in body


def test_public_and_confirmation_urls_are_restricted_to_https() -> None:
    with patch.dict(os.environ, {"YT_LOADER_PUBLIC_BASE_URL": "http://example.test"}):
        with pytest.raises(PaymentNotConfiguredError):
            public_base_url()
    assert validate_confirmation_url("https://yoomoney.ru/pay/123")
    with pytest.raises(PaymentValidationError):
        validate_confirmation_url("https://attacker.example/pay/123")
