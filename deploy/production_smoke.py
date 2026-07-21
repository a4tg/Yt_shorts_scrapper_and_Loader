#!/usr/bin/env python3
"""Non-destructive smoke checks for a deployed All As Planned instance."""

from __future__ import annotations

import argparse
import json
import os
import ssl
from http.cookiejar import CookieJar
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener


REQUIRED_AI_FEATURES = {"text", "image", "transcription", "clips"}
REQUIRED_LEGAL_DOCUMENTS = {
    "terms",
    "offer",
    "privacy",
    "personal_data_consent",
    "refund_policy",
    "storage_policy",
}


class SmokeFailure(RuntimeError):
    pass


class SmokeClient:
    def __init__(self, base_url: str, *, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.origin = self.base_url.rstrip("/")
        self.timeout = timeout
        self.opener = build_opener(
            HTTPCookieProcessor(CookieJar()),
            HTTPSHandler(context=ssl.create_default_context()),
        )

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> bytes:
        body = None
        headers = {"Accept": "application/json", "Origin": self.origin}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            urljoin(self.base_url, path.lstrip("/")),
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.status < 200 or response.status >= 300:
                    raise SmokeFailure(f"{method} {path}: HTTP {response.status}")
                return response.read()
        except HTTPError as exc:
            raise SmokeFailure(f"{method} {path}: HTTP {exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            raise SmokeFailure(f"{method} {path}: connection failed") from exc

    def json(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> Any:
        raw = self._request(path, method=method, payload=payload)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SmokeFailure(f"{method} {path}: invalid JSON response") from exc

    def page(self, path: str) -> None:
        self._request(path)


def _expect(condition: object, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def run_checks(
    client: Any,
    *,
    require_ai: bool,
    commercial: bool,
    email: str = "",
    password: str = "",
) -> list[str]:
    completed: list[str] = []

    health = client.json("/api/health")
    _expect(health.get("status") == "ok", "Health endpoint is not OK")
    completed.append("application health")

    ready = client.json("/api/health/ready")
    for key in ("status", "database", "workers", "disk"):
        _expect(ready.get(key) == "ok", f"Readiness check failed: {key}")
    completed.append("database, workers and disk readiness")

    auth = client.json("/api/auth/config")
    for key in (
        "registration_enabled",
        "email_verification_required",
        "email_delivery_enabled",
        "password_reset_enabled",
        "legal_acceptance_required",
    ):
        _expect(auth.get(key) is True, f"Authentication production flag is disabled: {key}")
    completed.append("registration, email verification and password reset")

    legal = client.json("/api/legal/config")
    documents = legal.get("documents") or {}
    _expect(REQUIRED_LEGAL_DOCUMENTS <= set(documents), "Legal document map is incomplete")
    for path in documents.values():
        client.page(str(path))
    if commercial:
        _expect(legal.get("complete") is True, "Legal seller configuration is incomplete")
        _expect(legal.get("acceptance_required") is True, "Legal acceptance is not required")
    completed.append("legal documents")

    needs_account = require_ai or commercial
    if needs_account:
        _expect(email and password, "AAP_SMOKE_EMAIL and AAP_SMOKE_PASSWORD are required")
    if email and password:
        client.json(
            "/api/auth/login",
            method="POST",
            payload={"email": email, "password": password},
        )
        me = client.json("/api/auth/me")
        _expect(str(me.get("email", "")).lower() == email.lower(), "Smoke account login mismatch")
        _expect(me.get("email_verified") is True, "Smoke account email is not verified")
        workspaces = client.json("/api/workspaces")
        _expect(isinstance(workspaces, list) and workspaces, "Smoke account has no workspace")
        completed.append("verified account and workspace access")

    if require_ai:
        ai = client.json("/api/ai/config")
        _expect(ai.get("enabled") is True, "AI provider is disabled")
        missing = REQUIRED_AI_FEATURES - set(ai.get("features") or [])
        _expect(not missing, "AI capabilities are missing: " + ", ".join(sorted(missing)))
        for key in ("text_model", "image_model", "transcription_model"):
            _expect(ai.get(key), f"AI model is not configured: {key}")
        completed.append("AI provider and capabilities")

    if commercial:
        payments = client.json("/api/payments/config")
        _expect(payments.get("enabled") is True, "YooKassa payments are disabled")
        _expect(payments.get("legal_ready") is True, "Payment legal configuration is incomplete")
        plans = client.json("/api/billing/plans")
        expected_plans = {
            "free": (0, 20),
            "creator": (149_000, 200),
            "studio": (449_000, 700),
            "agency": (999_000, 1_800),
        }
        actual_plans = {
            str(item.get("id")): (
                int(item.get("price_minor", -1)),
                int(item.get("monthly_credits", -1)),
            )
            for item in plans
            if isinstance(item, dict)
        }
        _expect(actual_plans == expected_plans, "Commercial plan catalog is inconsistent")
        packages = client.json("/api/payments/credit-packages")
        expected_packages = {
            "credits_100": (89_000, 100),
            "credits_500": (349_000, 500),
            "credits_1500": (849_000, 1_500),
        }
        actual_packages = {
            str(item.get("id")): (
                int(item.get("price_minor", -1)),
                int(item.get("credits", -1)),
            )
            for item in packages
            if isinstance(item, dict)
        }
        _expect(actual_packages == expected_packages, "Credit package catalog is inconsistent")
        completed.append("YooKassa, commercial plans and credit packages")

    return completed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default=os.getenv("YT_LOADER_PUBLIC_BASE_URL", "https://allasplanned.ru"),
    )
    parser.add_argument("--require-ai", action="store_true")
    parser.add_argument("--commercial", action="store_true")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()

    parsed = urlparse(args.base_url)
    if parsed.scheme != "https" or not parsed.hostname:
        print("Production smoke: FAIL\n- --base-url must be a complete HTTPS URL")
        return 1

    email = os.getenv("AAP_SMOKE_EMAIL", "").strip()
    password = os.getenv("AAP_SMOKE_PASSWORD", "")
    try:
        completed = run_checks(
            SmokeClient(args.base_url, timeout=max(1.0, args.timeout)),
            require_ai=args.require_ai,
            commercial=args.commercial,
            email=email,
            password=password,
        )
    except SmokeFailure as exc:
        print(f"Production smoke: FAIL\n- {exc}")
        return 1
    finally:
        password = ""

    print("Production smoke: PASS")
    for item in completed:
        print(f"- {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
