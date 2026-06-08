"""LLM provider (E1) — OpenAI-호환 호출·graceful-degrade·env config. 키리스(MockTransport)."""

from __future__ import annotations

import httpx
import pytest

from app.enrich.provider import (
    OpenAICompatibleProvider,
    ProviderError,
    provider_from_env,
)


def _client(handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_complete_success() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path.endswith("/chat/completions")
        return httpx.Response(200, json={"choices": [{"message": {"content": "추출결과"}}]})

    p = OpenAICompatibleProvider("http://spark/v1", "m", api_key="k", client=_client(handler))
    assert p.complete("sys", "user") == "추출결과"


def test_http_error_becomes_provider_error() -> None:
    # 429/5xx 등 비200 → ProviderError(graceful-degrade 신호, crash 아님)
    p = OpenAICompatibleProvider(
        "http://spark/v1", "m", client=_client(lambda r: httpx.Response(429, json={}))
    )
    with pytest.raises(ProviderError):
        p.complete("s", "u")


def test_malformed_response_becomes_provider_error() -> None:
    # 200이지만 형식 깨짐(choices 없음) → ProviderError
    p = OpenAICompatibleProvider(
        "http://spark/v1", "m", client=_client(lambda r: httpx.Response(200, json={"x": 1}))
    )
    with pytest.raises(ProviderError):
        p.complete("s", "u")


def test_transport_error_becomes_provider_error() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    p = OpenAICompatibleProvider("http://spark/v1", "m", client=_client(boom))
    with pytest.raises(ProviderError):
        p.complete("s", "u")


def test_provider_from_env_unconfigured_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENRICH_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("ENRICH_LLM_MODEL", raising=False)
    assert provider_from_env() is None  # 미구성 → stub 경로 유지(키리스 게이트 불변)


def test_provider_from_env_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    # Spark vs API = config(코드 불변): base_url+model만 다르면 됨
    monkeypatch.setenv("ENRICH_LLM_BASE_URL", "http://spark/v1")
    monkeypatch.setenv("ENRICH_LLM_MODEL", "spark-1")
    p = provider_from_env()
    assert isinstance(p, OpenAICompatibleProvider)
    assert p.base_url == "http://spark/v1" and p.model == "spark-1"
