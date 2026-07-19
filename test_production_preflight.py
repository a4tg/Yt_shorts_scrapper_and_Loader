from deploy.production_preflight import load_env, validate


def complete_env() -> dict[str, str]:
    return {
        "POSTGRES_PASSWORD": "x" * 32,
        "YT_LOADER_PUBLIC_BASE_URL": "https://allasplanned.ru",
        "YT_LOADER_ALLOWED_HOSTS": "allasplanned.ru,localhost,127.0.0.1",
        "YT_LOADER_SECURE_COOKIES": "true",
        "YT_LOADER_ENABLE_API_DOCS": "false",
        "YT_LOADER_LEGACY_BASIC_AUTH": "false",
        "YT_LOADER_REQUIRE_EMAIL_VERIFICATION": "true",
        "SMTP_HOST": "smtp.example.com",
        "SMTP_USERNAME": "no-reply@example.com",
        "SMTP_PASSWORD": "secret",
        "SMTP_FROM_EMAIL": "no-reply@example.com",
        "AAP_AI_API_KEY": "ai-secret",
        "YT_LOADER_FEATURE_WORKSPACE_DEPTH_SHELL": "true",
        "YT_LOADER_FEATURE_CHAT_ANYWHERE": "true",
        "YT_LOADER_FEATURE_ASSET_VIEWER": "true",
        "YT_LOADER_FEATURE_ASSET_REVIEWS": "true",
        "YT_LOADER_FEATURE_PROJECT_GRAPH": "true",
        "YT_LOADER_FEATURE_DECISION_INTELLIGENCE": "true",
        "YT_LOADER_ENABLE_PAYMENTS": "true",
        "YT_LOADER_REQUIRE_LEGAL_ACCEPTANCE": "true",
        "YT_LOADER_LEGAL_DOCUMENTS_APPROVED": "true",
        "YT_LOADER_LEGAL_SELLER_NAME": "Seller",
        "YT_LOADER_LEGAL_SELLER_INN": "1234567890",
        "YT_LOADER_LEGAL_SELLER_ADDRESS": "Address",
        "YT_LOADER_LEGAL_SUPPORT_EMAIL": "support@example.com",
        "YT_LOADER_LEGAL_VERSION": "2026-07-19",
        "YOOKASSA_SHOP_ID": "shop",
        "YOOKASSA_SECRET_KEY": "payment-secret",
        "YOOKASSA_WEBHOOK_ENFORCE_IP": "true",
    }


def test_production_preflight_accepts_complete_commercial_env() -> None:
    assert validate(complete_env(), commercial=True) == []


def test_production_preflight_reports_incomplete_product_without_secrets() -> None:
    values = complete_env()
    values["YT_LOADER_FEATURE_PROJECT_GRAPH"] = "false"
    values["AAP_AI_API_KEY"] = ""
    values["YOOKASSA_SECRET_KEY"] = ""

    errors = validate(values, commercial=True)

    assert any("PROJECT_GRAPH" in error for error in errors)
    assert any("AI_API_KEY" in error for error in errors)
    assert "YOOKASSA_SECRET_KEY must be configured" in errors


def test_load_env_supports_comments_quotes_and_equals(tmp_path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        '# comment\nPUBLIC="https://example.com"\nSECRET=abc=123\n',
        encoding="utf-8",
    )
    assert load_env(path) == {
        "PUBLIC": "https://example.com",
        "SECRET": "abc=123",
    }
