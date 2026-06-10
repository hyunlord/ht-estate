"""평판 RAG 읽기 측 (E3-3) — retrieval+rerank+종합 + graceful 3분기 + DB권 + 라우트. 키리스.

실 embed/rerank/gemma/네트워크 0(mock 주입). read-only(canon write 0) → 지문/counts 불변.
graceful: embed down→PENDING · rerank down→KNN fallback · gemma down→인용만 · 어느 것도 crash 0.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.corpus.builder import build_corpus
from app.embed.client import (
    EMBED_DIM,
    EmbedRecipe,
    EmbedResult,
    EmbedUnavailable,
    RerankHit,
    RerankUnavailable,
)
from app.enrich.fetcher import SourceDoc
from app.enrich.provider import ProviderError
from app.reputation.service import (
    PENDING,
    READY,
    synthesize_reputation,
)
from app.store.db import get_connection, init_db

NOW = datetime(2026, 6, 11, tzinfo=UTC)
RECIPE = EmbedRecipe(embed_model="bge-m3", dim=EMBED_DIM, normalized=True)


# ── one-hot fakes(KNN 결정론) ──
def _vec(idx: int) -> list[float]:
    v = [0.0] * EMBED_DIM
    v[idx] = 1.0
    return v


def _kw_idx(text: str) -> int:
    if "주차" in text:
        return 0
    if "소음" in text:
        return 1
    return 2


@dataclass
class FakeEmbed:
    """embed + rerank 겸용(실 EmbedClient 동형) — 라우트 deps.embed_client로 둘 다 충족."""

    embed_model: str = "bge-m3"
    base_url: str = "http://test/v1"
    down: bool = False

    def embed(self, texts: list[str]) -> EmbedResult:
        if self.down:
            raise EmbedUnavailable("down")
        return EmbedResult(vectors=[_vec(_kw_idx(t)) for t in texts], recipe=RECIPE)

    def rerank(self, query, documents, top_n=None):  # type: ignore[no-untyped-def]
        n = len(documents)
        hits = [RerankHit(index=i, score=float(n - i)) for i in range(n)]
        return hits[: (top_n or n)]


@dataclass
class FakeRerank:
    """rerank — 입력 순서 역순으로 점수(결정론). down=True면 RerankUnavailable."""

    down: bool = False

    def rerank(self, query, documents, top_n=None):  # type: ignore[no-untyped-def]
        if self.down:
            raise RerankUnavailable("down")
        n = len(documents)
        hits = [RerankHit(index=i, score=float(n - i)) for i in range(n)]
        return hits[: (top_n or n)]


@dataclass
class FakeProvider:
    raw: str = "주차가 넉넉하다는 평과 부족하다는 평이 함께 언급됨."
    calls: int = 0

    def complete(self, system: str, user: str, /) -> str:
        self.calls += 1
        return self.raw


class DownProvider:
    def complete(self, system: str, user: str, /) -> str:
        raise ProviderError("down")


def _docs() -> list[SourceDoc]:
    return [
        SourceDoc(source_type="blog", source_url="https://blog/1", text="주차 정말 넉넉해요 여유"),
        SourceDoc(source_type="cafe", source_url="https://cafe/2", text="층간소음 민원이 잦다는"),
        SourceDoc(source_type="blog", source_url="https://blog/3", text="관리 상태 양호한 편"),
    ]


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.execute(
        "INSERT INTO complex (complex_id, name, property_type) VALUES ('C1', '가단지', 'apartment')"
    )
    conn.commit()
    # 코퍼스 적재(헬리오시티 라이브 동형) — review_chunk/_vec 채움.
    build_corpus(conn, "C1", "가단지", fetcher=_F(_docs()), embed_client=FakeEmbed(), now=NOW)
    return conn


@dataclass
class _F:
    docs: list[SourceDoc]

    def fetch(self, query: str, *, kind: str) -> list[SourceDoc]:
        return list(self.docs)


# ── service: retrieval + rerank + synth ──
def test_synth_full_retrieve_rerank_synth(db: sqlite3.Connection) -> None:
    prov = FakeProvider()
    r = synthesize_reputation(
        db, "C1", "주차 어때", embed_client=FakeEmbed(), rerank_client=FakeRerank(), provider=prov,
    )
    assert r.status == READY
    assert r.summary and "주차" in r.summary
    assert r.degraded == []
    assert prov.calls == 1
    # 인용: source_url + span_ref(딥링크 정밀)
    assert all(c.source_url.startswith("http") for c in r.citations)
    assert all(c.span_ref is not None for c in r.citations)


def test_retrieval_complex_filtered(db: sqlite3.Connection) -> None:
    # 다른 단지 코퍼스가 있어도 C1만 retrieve(complex 필터 KNN).
    db.execute(
        "INSERT INTO complex (complex_id, name, property_type) VALUES ('C2', '나단지', 'apartment')"
    )
    db.commit()
    build_corpus(db, "C2", "나단지", fetcher=_F([
        SourceDoc(source_type="blog", source_url="https://other/x", text="주차 관련 다른단지"),
    ]), embed_client=FakeEmbed(), now=NOW)
    r = synthesize_reputation(
        db, "C1", "주차", embed_client=FakeEmbed(), rerank_client=FakeRerank(),
        provider=FakeProvider(),
    )
    assert all(c.source_url != "https://other/x" for c in r.citations)  # C2 청크 제외
    assert r.citations  # C1 청크는 있음


# ── graceful 3분기 ──
def test_graceful_embed_down_pending(db: sqlite3.Connection) -> None:
    r = synthesize_reputation(
        db, "C1", "주차", embed_client=FakeEmbed(down=True), rerank_client=FakeRerank(),
        provider=FakeProvider(),
    )
    assert r.status == PENDING and r.summary is None and "embed" in r.degraded  # crash 0


def test_graceful_rerank_down_knn_fallback(db: sqlite3.Connection) -> None:
    prov = FakeProvider()
    r = synthesize_reputation(
        db, "C1", "주차", embed_client=FakeEmbed(), rerank_client=FakeRerank(down=True),
        provider=prov,
    )
    assert r.status == READY and "rerank" in r.degraded  # KNN 순서로 진행
    assert r.summary is not None and r.citations  # 종합은 계속(crash 0)


def test_graceful_gemma_down_evidence_only(db: sqlite3.Connection) -> None:
    r = synthesize_reputation(
        db, "C1", "주차", embed_client=FakeEmbed(), rerank_client=FakeRerank(),
        provider=DownProvider(),
    )
    assert r.status == READY and r.summary is None  # 인용만(evidence-only)
    assert r.citations and "synth" in r.degraded  # crash 0


def test_graceful_provider_none_evidence_only(db: sqlite3.Connection) -> None:
    r = synthesize_reputation(
        db, "C1", "주차", embed_client=FakeEmbed(), rerank_client=FakeRerank(), provider=None,
    )
    assert r.status == READY and r.summary is None and r.citations and "synth" in r.degraded


# ── DB권: 종합 길이 가드(원문 대량 재현 방지) ──
def test_db_rights_summary_length_guard(db: sqlite3.Connection) -> None:
    huge = FakeProvider(raw="가" * 5000)  # 원문 대량 흉내
    r = synthesize_reputation(
        db, "C1", "주차", embed_client=FakeEmbed(), rerank_client=FakeRerank(), provider=huge,
    )
    assert r.summary is not None and len(r.summary) <= 600  # SUMMARY_MAX_CHARS 가드


# ── 라우트 (TestClient·dependency_overrides 키리스) ──
@dataclass
class _Corpus:
    """OnDemandCorpus mock — state 고정 반환(라우트 분기 검증)."""

    state: str
    seen: list = field(default_factory=list)

    def ensure(self, conn, complex_id, name, *, now=None):  # type: ignore[no-untyped-def]
        self.seen.append((complex_id, name))
        return self.state


@dataclass
class _Deps:
    corpus: _Corpus
    embed_client: FakeEmbed
    provider: object


def _client(db: sqlite3.Connection, deps: _Deps) -> TestClient:
    from app.main import app, get_db, get_reputation

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_reputation] = lambda: deps
    return TestClient(app)


def test_route_ready_returns_summary_and_citations(db: sqlite3.Connection) -> None:
    deps = _Deps(_Corpus(READY), FakeEmbed(), FakeProvider())
    client = _client(db, deps)
    try:
        resp = client.post("/complexes/C1/reputation", json={"query": "주차 어때"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ready" and body["summary"]
        assert body["citations"] and body["citations"][0]["span_ref"] is not None
    finally:
        from app.main import app

        app.dependency_overrides.clear()


def test_route_corpus_miss_pending(db: sqlite3.Connection) -> None:
    deps = _Deps(_Corpus(PENDING), FakeEmbed(), FakeProvider())
    client = _client(db, deps)
    try:
        resp = client.post("/complexes/C1/reputation", json={"query": "주차"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "pending" and body["summary"] is None and body["citations"] == []
        assert deps.corpus.seen == [("C1", "가단지")]  # 코퍼스 트리거 시도
    finally:
        from app.main import app

        app.dependency_overrides.clear()


def test_route_404_unknown_complex(db: sqlite3.Connection) -> None:
    deps = _Deps(_Corpus(READY), FakeEmbed(), FakeProvider())
    client = _client(db, deps)
    try:
        resp = client.post("/complexes/NOPE/reputation", json={"query": "주차"})
        assert resp.status_code == 404
    finally:
        from app.main import app

        app.dependency_overrides.clear()
