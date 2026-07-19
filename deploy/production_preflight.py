#!/usr/bin/env python3
"""Validate a production .env without printing secrets."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlparse


TRUE_VALUES = {"1", "true", "yes", "on"}
FEATURE_KEYS = (
    "YT_LOADER_FEATURE_WORKSPACE_DEPTH_SHELL",
    "YT_LOADER_FEATURE_CHAT_ANYWHERE",
    "YT_LOADER_FEATURE_ASSET_VIEWER",
    "YT_LOADER_FEATURE_ASSET_REVIEWS",
    "YT_LOADER_FEATURE_PROJECT_GRAPH",
    "YT_LOADER_FEATURE_DECISION_INTELLIGENCE",
)
AI_FEATURES = {"text", "image", "transcription", "clips"}
AI_API_MODES = {"auto", "responses", "chat_completions"}


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def enabled(values: dict[str, str], key: str) -> bool:
    return values.get(key, "").strip().lower() in TRUE_VALUES


def validate(values: dict[str, str], *, commercial: bool) -> list[str]:
    errors: list[str] = []

    def require(key: str, message: str | None = None) -> None:
        if not values.get(key, "").strip():
            errors.append(message or f"{key} must be configured")

    base_url = values.get("YT_LOADER_PUBLIC_BASE_URL", "").strip()
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.hostname:
        errors.append("YT_LOADER_PUBLIC_BASE_URL must be a complete HTTPS URL")
    allowed_hosts = {
        item.strip().lower()
        for item in values.get("YT_LOADER_ALLOWED_HOSTS", "").split(",")
        if item.strip()
    }
    if parsed.hostname and parsed.hostname.lower() not in allowed_hosts:
        errors.append("YT_LOADER_ALLOWED_HOSTS must contain the public hostname")
    if not enabled(values, "YT_LOADER_SECURE_COOKIES"):
        errors.append("YT_LOADER_SECURE_COOKIES must be true")
    if enabled(values, "YT_LOADER_ENABLE_API_DOCS"):
        errors.append("YT_LOADER_ENABLE_API_DOCS must be false")
    if enabled(values, "YT_LOADER_LEGACY_BASIC_AUTH"):
        errors.append("YT_LOADER_LEGACY_BASIC_AUTH must be false")
    if not enabled(values, "YT_LOADER_REQUIRE_EMAIL_VERIFICATION"):
        errors.append("YT_LOADER_REQUIRE_EMAIL_VERIFICATION must be true")
    if len(values.get("POSTGRES_PASSWORD", "")) < 24:
        errors.append("POSTGRES_PASSWORD must contain at least 24 characters")
    for key in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM_EMAIL"):
        require(key)
    for key in FEATURE_KEYS:
        if not enabled(values, key):
            errors.append(f"{key} must be true for the complete product")

    ai_key = values.get("AAP_AI_API_KEY", "") or values.get("OPENAI_API_KEY", "")
    if not ai_key:
        errors.append("AAP_AI_API_KEY or OPENAI_API_KEY must be configured")
    ai_base_url = (
        values.get("AAP_AI_BASE_URL", "").strip()
        or values.get("OPENAI_BASE_URL", "").strip()
        or "https://api.openai.com/v1"
    )
    ai_parsed = urlparse(ai_base_url)
    if ai_parsed.scheme != "https" or not ai_parsed.hostname:
        errors.append("AAP_AI_BASE_URL or OPENAI_BASE_URL must be a complete HTTPS URL")
    ai_mode = (
        values.get("AAP_AI_API_MODE", "").strip()
        or values.get("OPENAI_API_MODE", "").strip()
        or "auto"
    ).lower()
    if ai_mode not in AI_API_MODES:
        errors.append("AAP_AI_API_MODE must be auto, responses or chat_completions")
    configured_ai_features = {
        item.strip().lower()
        for item in (
            values.get("AAP_AI_FEATURES", "")
            or values.get("OPENAI_FEATURES", "")
            or ",".join(sorted(AI_FEATURES))
        ).split(",")
        if item.strip()
    }
    missing_ai_features = sorted(AI_FEATURES - configured_ai_features)
    if missing_ai_features:
        errors.append(
            "AAP_AI_FEATURES must enable the complete product: "
            + ", ".join(missing_ai_features)
        )
    for key, legacy_key in (
        ("AAP_AI_TEXT_MODEL", "OPENAI_TEXT_MODEL"),
        ("AAP_AI_IMAGE_MODEL", "OPENAI_IMAGE_MODEL"),
        ("AAP_AI_TRANSCRIPTION_MODEL", "OPENAI_TRANSCRIPTION_MODEL"),
    ):
        if not (values.get(key, "").strip() or values.get(legacy_key, "").strip()):
            errors.append(f"{key} or {legacy_key} must be configured")

    if commercial:
        if not enabled(values, "YT_LOADER_ENABLE_PAYMENTS"):
            errors.append("YT_LOADER_ENABLE_PAYMENTS must be true")
        if not enabled(values, "YT_LOADER_REQUIRE_LEGAL_ACCEPTANCE"):
            errors.append("YT_LOADER_REQUIRE_LEGAL_ACCEPTANCE must be true")
        if not enabled(values, "YT_LOADER_LEGAL_DOCUMENTS_APPROVED"):
            errors.append("YT_LOADER_LEGAL_DOCUMENTS_APPROVED must be true")
        for key in (
            "YT_LOADER_LEGAL_SELLER_NAME",
            "YT_LOADER_LEGAL_SELLER_INN",
            "YT_LOADER_LEGAL_SELLER_ADDRESS",
            "YT_LOADER_LEGAL_SUPPORT_EMAIL",
            "YT_LOADER_LEGAL_VERSION",
            "YOOKASSA_SHOP_ID",
            "YOOKASSA_SECRET_KEY",
        ):
            require(key)
        if not enabled(values, "YOOKASSA_WEBHOOK_ENFORCE_IP"):
            errors.append("YOOKASSA_WEBHOOK_ENFORCE_IP must be true")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--commercial", action="store_true")
    args = parser.parse_args()
    path = Path(args.env_file)
    if not path.is_file():
        print(f"Production preflight: FAIL\n- env file not found: {path}")
        return 1
    errors = validate(load_env(path), commercial=args.commercial)
    if errors:
        print("Production preflight: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    mode = "commercial" if args.commercial else "closed beta"
    print(f"Production preflight: PASS ({mode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
