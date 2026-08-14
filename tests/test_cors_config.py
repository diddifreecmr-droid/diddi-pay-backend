from __future__ import annotations

import re

from payfund_app.core.config import Settings


def test_cors_regex_allows_localhost_and_diddifree_and_vercel():
    settings = Settings(cors_origins="*")
    regex = settings.cors_origin_regex

    assert "localhost" in regex
    assert "diddifree\\.com" in regex
    assert "vercel\\.com" in regex


def test_cors_regex_honors_explicit_origins():
    settings = Settings(cors_origins="https://app.example.com,https://admin.example.com")
    regex = settings.cors_origin_regex

    assert re.fullmatch(regex, "https://app.example.com")
    assert re.fullmatch(regex, "https://admin.example.com")
    assert re.fullmatch(regex, "http://localhost:5173")
    assert re.fullmatch(regex, "https://pay-api-staging.diddifree.com")
    assert re.fullmatch(regex, "https://preview-123.vercel.com")
    assert not re.fullmatch(regex, "https://evil.example.net")


def test_cors_explicit_origins_are_escaped_as_literals():
    regex = Settings(cors_origins="https://app.example.com").cors_origin_regex

    assert re.fullmatch(regex, "https://app.example.com")
    assert not re.fullmatch(regex, "https://appXexampleYcom")
