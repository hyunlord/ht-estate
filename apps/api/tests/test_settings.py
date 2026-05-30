"""API 키 로드 — 미설정 시 명확한 에러, 설정 시 반환. (라이브 키 불필요)"""

from __future__ import annotations

import pytest

from app.settings import API_KEY_ENV, MissingApiKeyError, get_api_key


def test_get_api_key_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    with pytest.raises(MissingApiKeyError):
        get_api_key()


def test_get_api_key_blank_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_KEY_ENV, "   ")
    with pytest.raises(MissingApiKeyError):
        get_api_key()


def test_get_api_key_present_returns_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_KEY_ENV, "  decoded-service-key  ")
    assert get_api_key() == "decoded-service-key"
