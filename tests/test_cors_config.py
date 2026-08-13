from __future__ import annotations

from payfund_app.core.config import Settings


def test_cors_regex_allows_localhost_and_diddifree_and_vercel():
    settings = Settings(cors_origins="*")
    regex = settings.cors_origin_regex

    assert "localhost" in regex
    assert "diddifree\\.com" in regex
    assert "vercel\\.com" in regex


def test_cors_regex_honors_explicit_origins():
    settings = Settings(cors_origins="https://app.example.com,https://admin.example.com")

    assert settings.cors_origin_regex == r"^(?:https://app.example.com|https://admin.example.com)$"
