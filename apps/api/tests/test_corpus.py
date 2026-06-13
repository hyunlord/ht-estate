"""후기/평판 코퍼스 (E3-2) — 청킹·write-back·vec0 KNN·graceful·TTL·멱등·C47 락. 키리스.

실 embed/네트워크 0(FakeEmbed·FakeFetcher 또는 httpx.MockTransport 주입). review_chunk/_vec만
write → 지문/counts 불변. graceful(embed down→defer·소스 fail→skip·crash 0)·반쪽쓰기 0 검증.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.corpus.builder import (
    BUILT,
    EMBED_DEFERRED,
    FRESH,
    LOCK_YIELD,
    NO_RELEVANT,
    NO_SOURCE,
    build_corpus,
)
from app.corpus.chunker import chunk_doc, chunk_text
from app.corpus.ondemand import OnDemandCorpus
from app.corpus.store import (
    chunk_count,
    is_fresh,
    make_chunk_id,
    read_chunks,
)
from app.corpus.vec import knn
from app.embed.client import EMBED_DIM, EmbedClient, EmbedRecipe, EmbedResult, EmbedUnavailable
from app.enrich.fetcher import SourceDoc
from app.store.db import get_connection, init_db

NOW = datetime(2026, 6, 10, tzinfo=UTC)
RECIPE = EmbedRecipe(embed_model="bge-m3", dim=EMBED_DIM, normalized=True)


# ── fakes ──
def _vec(idx: int) -> list[float]:
    """1024차 one-hot(테스트 KNN 결정론) — idx 차원만 1.0."""
    v = [0.0] * EMBED_DIM
    v[idx] = 1.0
    return v


def _kw_idx(text: str) -> int:
    """텍스트 키워드 → one-hot idx. '주차'→0·'학군'→1·그외→2(KNN 분리 검증용)."""
    if "주차" in text:
        return 0
    if "학군" in text:
        return 1
    return 2


@dataclass
class FakeEmbed:
    """결정론 embed — 키워드 one-hot 벡터. down=True면 EmbedUnavailable(graceful defer 검증)."""

    embed_model: str = "bge-m3"
    base_url: str = "http://test/v1"
    down: bool = False
    calls: int = 0

    def embed(self, texts: list[str]) -> EmbedResult:
        self.calls += 1
        if self.down:
            raise EmbedUnavailable("test down")
        return EmbedResult(vectors=[_vec(_kw_idx(t)) for t in texts], recipe=RECIPE)


@dataclass
class FakeFetcher:
    """결정론 소스 페처 — 고정 docs 반환. empty=True면 무결과(NO_SOURCE 검증)."""

    docs: list[SourceDoc] = field(default_factory=list)
    fetched: int = 0

    def fetch(self, query: str, *, kind: str) -> list[SourceDoc]:
        self.fetched += 1
        return list(self.docs)


def _seed_build(conn: sqlite3.Connection, **kw):  # type: ignore[no-untyped-def]
    """초기 build 헬퍼(반복 축약) — 기본 fetcher/embed/now, kw override."""
    return build_corpus(conn, "C1", "가단지", fetcher=FakeFetcher(_docs()),
                        embed_client=FakeEmbed(), now=kw.pop("now", NOW), **kw)


def _docs() -> list[SourceDoc]:
    # rag-corpus-quality: 건물검증 게이트 통과하려 doc 텍스트에 단지명 포함(실 후기 형태).
    return [
        SourceDoc(source_type="blog", source_url="https://blog/1", text="가단지 주차 넉넉해요"),
        SourceDoc(source_type="cafe", source_url="https://cafe/2", text="가단지 학군 좋다는 평"),
    ]


@contextmanager
def _yielding_lock(acquired: bool):
    """C47 락 mock — acquired 값을 그대로 yield."""
    yield acquired


def _seed(conn: sqlite3.Connection) -> None:
    init_db(conn)
    conn.execute(
        "INSERT INTO complex (complex_id, name, property_type) VALUES ('C1', '가단지', 'apartment')"
    )
    conn.commit()


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    _seed(conn)
    return conn


@pytest.fixture
def file_db_path(tmp_path) -> str:  # type: ignore[no-untyped-def]
    """파일 DB 경로 — 백그라운드 _run이 자기 conn close해도 데이터 보존(공유 in-memory 불가)."""
    path = str(tmp_path / "corpus.db")
    conn = get_connection(path)
    _seed(conn)
    conn.close()
    return path


# ── chunker ──
def test_chunk_text_paragraphs_span_ref() -> None:
    chunks = chunk_text("첫 문단입니다.\n\n둘째 문단이에요.")
    assert [c.span_ref for c in chunks] == ["p0", "p1"]
    assert chunks[0].text == "첫 문단입니다." and chunks[1].text == "둘째 문단이에요."


def test_chunk_text_long_para_soft_split() -> None:
    long = " ".join([f"문장{i} 입니다." for i in range(60)])  # > max_chars
    chunks = chunk_text(long, max_chars=80)
    assert len(chunks) > 1  # 문장경계로 분할
    assert all(len(c.text) <= 80 for c in chunks)
    assert [c.span_ref for c in chunks] == [f"p{i}" for i in range(len(chunks))]


def test_chunk_text_empty_skipped() -> None:
    assert chunk_text("  \n\n  ") == []


def test_chunk_doc_uses_doc_text() -> None:
    doc = SourceDoc(source_type="blog", source_url="u", text="단락 하나")
    chunks = chunk_doc(doc)
    assert len(chunks) == 1 and chunks[0].text == "단락 하나"


# ── store: chunk_id 결정론 + 멱등 ──
def test_make_chunk_id_deterministic() -> None:
    a = make_chunk_id("C1", "https://x", "p0")
    b = make_chunk_id("C1", "https://x", "p0")
    c = make_chunk_id("C1", "https://x", "p1")
    assert a == b and a != c and len(a) == 24


# ── builder: 정상 build + vec KNN ──
def test_build_writes_chunks_and_vec_queryable(db: sqlite3.Connection) -> None:
    r = build_corpus(db, "C1", "가단지", fetcher=FakeFetcher(_docs()),
                     embed_client=FakeEmbed(), now=NOW)
    assert r.status == BUILT and r.chunks_written == 2 and r.docs_fetched == 2
    chunks = read_chunks(db, "C1")
    assert {c.source_type for c in chunks} == {"blog", "cafe"}
    assert all(c.span_ref == "p0" for c in chunks)  # 각 doc 1문단
    # ★레시피핀 적재 확인
    row = db.execute(
        "SELECT embed_model, embed_dim, embed_normalized FROM review_chunk LIMIT 1"
    ).fetchone()
    assert row["embed_model"] == "bge-m3" and row["embed_dim"] == EMBED_DIM
    # ★vec0 KNN: '주차' 쿼리(idx0) → 주차 청크가 top
    hits = knn(db, _vec(0), k=2)
    assert len(hits) == 2
    top_id = hits[0][0]
    top = db.execute("SELECT chunk_text FROM review_chunk WHERE chunk_id=?", (top_id,)).fetchone()
    assert "주차" in top["chunk_text"]  # 주차 관련 청크 최근접


def test_build_idempotent_no_dup(db: sqlite3.Connection) -> None:
    f, e = FakeFetcher(_docs()), FakeEmbed()
    build_corpus(db, "C1", "가단지", fetcher=f, embed_client=e, now=NOW)
    # 재실행(force) → 같은 위치=같은 chunk_id 덮어씀 → dup 0
    build_corpus(db, "C1", "가단지", fetcher=f, embed_client=e, now=NOW, force=True)
    assert chunk_count(db, "C1") == 2
    assert db.execute("SELECT COUNT(*) FROM review_chunk_vec").fetchone()[0] == 2


# ── builder: 신선/TTL/레시피 ──
def test_build_fresh_skips(db: sqlite3.Connection) -> None:
    _seed_build(db)
    e2 = FakeEmbed()
    r = build_corpus(db, "C1", "가단지", fetcher=FakeFetcher(_docs()), embed_client=e2, now=NOW)
    assert r.status == FRESH and e2.calls == 0  # 신선 → embed 호출 0


def test_build_ttl_expired_rebuilds(db: sqlite3.Connection) -> None:
    build_corpus(db, "C1", "가단지", fetcher=FakeFetcher(_docs()), embed_client=FakeEmbed(),
                 now=NOW, ttl=timedelta(weeks=1))
    later = NOW + timedelta(weeks=2)  # TTL(1주) 만료
    assert not is_fresh(db, "C1", RECIPE, now=later)
    r = build_corpus(db, "C1", "가단지", fetcher=FakeFetcher(_docs()), embed_client=FakeEmbed(),
                     now=later)
    assert r.status == BUILT


def test_build_recipe_mismatch_rebuilds(db: sqlite3.Connection) -> None:
    _seed_build(db)
    # 다른 모델 → 레시피 불일치 → stale(재임베딩 대상)
    other = FakeEmbed(embed_model="bge-m3-v2")
    assert not is_fresh(db, "C1", EmbedRecipe(embed_model="bge-m3-v2", dim=EMBED_DIM), now=NOW)
    r = build_corpus(db, "C1", "가단지", fetcher=FakeFetcher(_docs()), embed_client=other, now=NOW)
    assert r.status == BUILT


# ── builder: graceful-degrade ──
def test_build_embed_down_defers_no_write(db: sqlite3.Connection) -> None:
    # embed down → EMBED_DEFERRED·write 0·crash 0.
    r = build_corpus(db, "C1", "가단지", fetcher=FakeFetcher(_docs()),
                     embed_client=FakeEmbed(down=True), now=NOW)
    assert r.status == EMBED_DEFERRED and r.chunks_written == 0
    assert chunk_count(db, "C1") == 0  # 미기록


def test_build_embed_down_keeps_existing_cache(db: sqlite3.Connection) -> None:
    # 먼저 build 성공(캐시 존재) → TTL 만료 후 embed down → 기존 캐시 무손상(검색 지속).
    build_corpus(db, "C1", "가단지", fetcher=FakeFetcher(_docs()), embed_client=FakeEmbed(),
                 now=NOW, ttl=timedelta(weeks=1))
    later = NOW + timedelta(weeks=2)
    r = build_corpus(db, "C1", "가단지", fetcher=FakeFetcher(_docs()),
                     embed_client=FakeEmbed(down=True), now=later)
    assert r.status == EMBED_DEFERRED
    assert chunk_count(db, "C1") == 2  # 기존 캐시 유지(반쪽 0·무손상)


def test_build_no_source_defers(db: sqlite3.Connection) -> None:
    r = build_corpus(db, "C1", "가단지", fetcher=FakeFetcher([]), embed_client=FakeEmbed(), now=NOW)
    assert r.status == NO_SOURCE and chunk_count(db, "C1") == 0


# ── builder: rag-corpus-quality 건물검증+노이즈 필터(적재시) ──
def test_build_rejects_off_building_and_noise(db: sqlite3.Connection) -> None:
    # 딴 건물(가단지 미포함)·경매 광고만 → 전부 filter → NO_RELEVANT·write 0(오염 적재 0).
    docs = [
        SourceDoc(source_type="blog", source_url="u1", text="나단지 주차 넉넉해요"),  # 딴 단지
        SourceDoc(source_type="blog", source_url="u2", text="가단지 경매 감정가 입찰"),  # 노이즈
    ]
    r = build_corpus(db, "C1", "가단지", fetcher=FakeFetcher(docs),
                     embed_client=FakeEmbed(), now=NOW)
    assert r.status == NO_RELEVANT and chunk_count(db, "C1") == 0


def test_build_keeps_only_relevant_docs(db: sqlite3.Connection) -> None:
    # 혼합: 관련 1 + 딴단지 1 + 노이즈 1 → 관련 doc만 적재.
    docs = [
        SourceDoc(source_type="blog", source_url="u1", text="가단지 층간소음 적고 관리 좋아요"),
        SourceDoc(source_type="cafe", source_url="u2", text="다른단지 인테리어 시공"),  # 딴+노이즈
        SourceDoc(source_type="blog", source_url="u3", text="가단지 담보대출 잘 나와요"),  # 노이즈
    ]
    r = build_corpus(db, "C1", "가단지", fetcher=FakeFetcher(docs),
                     embed_client=FakeEmbed(), now=NOW)
    assert r.status == BUILT and r.chunks_written == 1  # 관련 후기 1건만


def test_build_classifier_rejects_borderline(db: sqlite3.Connection) -> None:
    # 룰 통과(가단지·후기)이나 LLM 분류기가 reject → 적재 0(precision 경계).
    docs = [SourceDoc(source_type="blog", source_url="u1", text="가단지 주차 넉넉")]
    r = build_corpus(db, "C1", "가단지", fetcher=FakeFetcher(docs), embed_client=FakeEmbed(),
                     now=NOW, classifier=lambda _t: False)
    assert r.status == NO_RELEVANT and chunk_count(db, "C1") == 0


def test_build_source_fetch_exception_defers(db: sqlite3.Connection) -> None:
    class BoomFetcher:
        def fetch(self, query: str, *, kind: str):  # type: ignore[no-untyped-def]
            raise httpx.ConnectError("boom")

    r = build_corpus(db, "C1", "가단지", fetcher=BoomFetcher(), embed_client=FakeEmbed(), now=NOW)
    assert r.status == NO_SOURCE and chunk_count(db, "C1") == 0  # crash 0


def test_build_lock_busy_yields_no_write(db: sqlite3.Connection) -> None:
    # C47 락 점유 → LOCK_YIELD·write 0(다음 트리거 resume).
    r = build_corpus(db, "C1", "가단지", fetcher=FakeFetcher(_docs()), embed_client=FakeEmbed(),
                     now=NOW, lock=lambda: _yielding_lock(False))
    assert r.status == LOCK_YIELD and chunk_count(db, "C1") == 0


def test_build_lock_acquired_writes(db: sqlite3.Connection) -> None:
    r = build_corpus(db, "C1", "가단지", fetcher=FakeFetcher(_docs()), embed_client=FakeEmbed(),
                     now=NOW, lock=lambda: _yielding_lock(True))
    assert r.status == BUILT and chunk_count(db, "C1") == 2


# ── builder: 실 EmbedClient + MockTransport graceful(5xx→defer) ──
def test_build_real_client_5xx_defers(db: sqlite3.Connection) -> None:
    tr = httpx.MockTransport(lambda req: httpx.Response(503, json={}))
    client = EmbedClient(client=httpx.Client(transport=tr), sleep=lambda _s: None, max_retries=1)
    r = build_corpus(db, "C1", "가단지", fetcher=FakeFetcher(_docs()), embed_client=client, now=NOW)
    assert r.status == EMBED_DEFERRED and chunk_count(db, "C1") == 0  # 재시도 소진→defer·crash 0


# ── ondemand: lazy 트리거 ──
def test_ondemand_fresh_ready(db: sqlite3.Connection) -> None:
    _seed_build(db)
    oc = OnDemandCorpus(fetcher=FakeFetcher(_docs()), embed_client=FakeEmbed())
    assert oc.ensure(db, "C1", "가단지", now=NOW) == "ready"


def test_ondemand_no_fetcher_unavailable(db: sqlite3.Connection) -> None:
    oc = OnDemandCorpus(fetcher=None, embed_client=FakeEmbed())
    assert oc.ensure(db, "C1", "가단지", now=NOW) == "unavailable"


def test_ondemand_miss_triggers_build(file_db_path: str) -> None:
    ran: list = []
    oc = OnDemandCorpus(
        fetcher=FakeFetcher(_docs()), embed_client=FakeEmbed(),
        submit=lambda fn: (ran.append(1), fn())[0],  # inline 실행
        conn_factory=lambda: get_connection(file_db_path),  # _run 자기 conn(close 안전)
    )
    reader = get_connection(file_db_path)
    state = oc.ensure(reader, "C1", "가단지", now=NOW)
    assert state == "pending" and ran  # 백그라운드 build 제출됨
    assert chunk_count(reader, "C1") == 2  # inline build 완료(커밋 반영)


def test_ondemand_cooldown_after_attempt(file_db_path: str) -> None:
    # 무소스 build → _attempted 기록 → 쿨다운 내 재요청은 재제출 안 함(pending·재build 0).
    submits: list = []
    oc = OnDemandCorpus(
        fetcher=FakeFetcher([]), embed_client=FakeEmbed(),  # 무소스
        submit=lambda fn: (submits.append(1), fn())[0],
        conn_factory=lambda: get_connection(file_db_path),
    )
    reader = get_connection(file_db_path)
    oc.ensure(reader, "C1", "가단지", now=NOW)
    oc.ensure(reader, "C1", "가단지", now=NOW + timedelta(minutes=1))  # 쿨다운 내
    assert len(submits) == 1  # 두 번째는 재제출 안 함
