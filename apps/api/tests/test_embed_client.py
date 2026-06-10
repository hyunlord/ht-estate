"""embed/rerank 클라이언트 (E3-1) — shape·파싱·레시피핀·graceful·swappable. 키리스(MockTransport).

실 모델 서빙 0(httpx mock). 클라만 — canon DB 무접촉.
"""

from __future__ import annotations

import httpx
import pytest

from app.embed.client import (
    EMBED_DIM,
    EmbedClient,
    EmbedUnavailable,
    RerankUnavailable,
    embed_client_from_env,
)


def _client(handler, **kw) -> EmbedClient:  # type: ignore[no-untyped-def]
    return EmbedClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _s: None, **kw
    )


# ── embed shape + 1024 파싱 + 레시피핀 ──
def test_embed_request_shape_and_parse() -> None:
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json
        assert req.url.path.endswith("/v1/embeddings")
        seen.update(json.loads(req.content))
        vecs = [[0.1] * EMBED_DIM for _ in seen["input"]]
        data = [{"embedding": v} for v in vecs]
        return httpx.Response(200, json={"model": "bge-m3", "data": data})

    texts = ["층간소음 어때", "주차 넉넉?"]
    res = _client(handler).embed(texts)
    assert seen == {"model": "bge-m3", "input": texts}  # OpenAI embeddings shape
    assert len(res.vectors) == 2 and len(res.vectors[0]) == EMBED_DIM
    assert res.recipe.embed_model == "bge-m3" and res.recipe.dim == 1024 and res.recipe.normalized


# ── rerank shape + score/index 파싱 + 내림차순 ──
def test_rerank_shape_and_sorted() -> None:
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json
        assert req.url.path.endswith("/rerank")
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"results": [
            {"index": 0, "score": 0.1}, {"index": 1, "score": 0.9}, {"index": 2, "score": 0.5},
        ]})

    hits = _client(handler).rerank("주차?", ["a", "b", "c"], top_n=3)
    assert seen == {"query": "주차?", "documents": ["a", "b", "c"], "top_n": 3}  # 우리 소유 shape
    assert [h.index for h in hits] == [1, 2, 0]  # score 내림차순
    assert hits[0].score == 0.9


# ── graceful-degrade (다운·타임아웃·5xx·4xx·malformed → typed Unavailable·crash 0) ──
def test_embed_connect_error_retries_then_unavailable() -> None:
    calls = {"n": 0}

    def boom(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("model down")

    with pytest.raises(EmbedUnavailable):  # crash(미처리 httpx) 아님
        _client(boom, max_retries=2).embed(["x"])
    assert calls["n"] == 3  # 1 + 2 재시도(backoff)


def test_embed_5xx_retries_then_unavailable() -> None:
    with pytest.raises(EmbedUnavailable):
        _client(lambda r: httpx.Response(503, json={}), max_retries=1).embed(["x"])


def test_embed_4xx_immediate_unavailable() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={})

    with pytest.raises(EmbedUnavailable):
        _client(handler, max_retries=2).embed(["x"])
    assert calls["n"] == 1  # 4xx → 재시도 안 함


def test_embed_malformed_unavailable() -> None:
    h = lambda r: httpx.Response(200, json={"nope": 1})  # data 없음  # noqa: E731
    with pytest.raises(EmbedUnavailable):
        _client(h).embed(["x"])


def test_rerank_down_unavailable() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(RerankUnavailable):
        _client(boom, max_retries=1).rerank("q", ["a", "b"])


# ── swappable config (기본 Spark-native·override) ──
def test_from_env_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("EMBED_BASE_URL", "EMBED_MODEL", "RERANK_MODEL"):
        monkeypatch.delenv(k, raising=False)
    c = embed_client_from_env()
    assert c.base_url == "http://localhost:8092/v1" and c.embed_model == "bge-m3"
    monkeypatch.setenv("EMBED_BASE_URL", "https://api.example/v1")
    monkeypatch.setenv("EMBED_MODEL", "text-embedding-3")
    c2 = embed_client_from_env()
    assert c2.base_url == "https://api.example/v1" and c2.embed_model == "text-embedding-3"
