"""코퍼스 build 코어 (E3-2) — fetch→청킹→embed→write-back. 후보-한정·멱등·graceful·C47 공존.

검색 동기패스 아님 — 후보 detail 트리거가 단건 build(전 172k 사전임베딩 0). 신선(TTL+레시피) 시
skip. graceful-degrade(세션 교훈, 의뢰서 선제 명시):
  · 소스 fetch 실패(Naver 429/타임아웃) → 해당 소스 빈결과 흡수(fetcher가 per-vertical skip)·
    전부 비면 build 안 함(defer·다음 트리거 재시도) — 반쪽 0.
  · embed down(EmbedUnavailable) → **새 chunk 임베딩 defer**(write skip·기존 캐시 유지·crash 0).
  · C47 락 점유 → 이번 build 양보(다음 트리거 resume).
write는 write_chunks 단일 트랜잭션(embed 성공 후) → 반쪽쓰기 0. review_chunk/_vec만 write.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.corpus.chunker import chunk_doc
from app.corpus.relevance import ChunkClassifier, filter_docs, region_tokens
from app.corpus.store import PendingChunk, _delete_complex, is_fresh, write_chunks
from app.embed.client import Embedder, EmbedUnavailable
from app.enrich.fetcher import SourceFetcher

# build 결과 상태.
BUILT = "built"               # 청크 적재 완료
FRESH = "fresh"               # 신선(TTL+레시피 유효) → skip
NO_SOURCE = "no_source"       # 소스 0건(전부 실패/무결과) → defer
NO_RELEVANT = "no_relevant"   # 건물검증+노이즈 필터 후 0(오염 reject)
EMBED_DEFERRED = "embed_deferred"  # embed down → defer(write skip·캐시 유지)
LOCK_YIELD = "lock_yield"     # C47 락 점유 → 양보(다음 트리거 resume)

DEFAULT_TTL = timedelta(weeks=3)  # 후기 신선도(수주)


@dataclass(frozen=True)
class BuildResult:
    status: str
    chunks_written: int = 0
    docs_fetched: int = 0


@contextmanager
def _null_lock() -> Iterator[bool]:
    """락 미주입 시 기본(항상 acquire) — 테스트/단독 실행용. 라이브는 ShlockBatch 주입(C47)."""
    yield True


def build_corpus(
    conn: sqlite3.Connection,
    complex_id: str,
    name: str,
    *,
    fetcher: SourceFetcher,
    embed_client: Embedder,
    now: datetime,
    ttl: timedelta = DEFAULT_TTL,
    lock: Callable[[], object] | None = None,
    max_chunks: int = 40,
    force: bool = False,
    classifier: ChunkClassifier | None = None,
) -> BuildResult:
    """단지 1건 코퍼스 build. fetch→건물검증+노이즈필터→청킹→embed→(C47 락)write. graceful·멱등.

    name=단지명(소스 쿼리). classifier=선택 LLM doc 분류기(룰 통과분 precision 재확인). lock=C47.
    """
    # 1) 신선하면 skip(레시피+TTL) — 모델/만료 시에만 rebuild. 레시피는 클라 config로 결정.
    if not force and is_fresh(conn, complex_id, _recipe_of(embed_client), now=now):
        return BuildResult(FRESH)

    # 2) 소스 fetch(graceful: fetcher가 per-source 실패 흡수 → 부분/빈).
    fetch_ok = True
    try:
        docs = fetcher.fetch(name, kind="review")
    except Exception:  # noqa: BLE001 — 예기치 못한 fetch 실패도 defer(crash 0)
        docs = []
        fetch_ok = False
    # ★ transient 가드(recall): fetch 실패/쿼터/빈결과면 NO_SOURCE(기존 유지·정화 금지).
    # "fetch 성공·진짜 후기 없음"만 clean-empty 정화. quota는 부분결과여도 transient.
    quota = bool(getattr(fetcher, "quota_blocked", False))
    if not docs or not fetch_ok or quota:
        return BuildResult(NO_SOURCE, docs_fetched=len(docs))

    # 2b) 건물검증(단지명+지역) + 노이즈 필터(경매/인테리어/대출) + 선택 LLM.
    # 지역(sigungu/dong) complex서 read(없으면 이름만). region 강등(rag-corpus-recall): distinctive
    # 이름은 doc에 동 불요(gemma 디스앰비)·generic만 region 하드. 오염(스위첸·파라곤)은 코어 reject.
    row = conn.execute(
        "SELECT sigungu, dong FROM complex WHERE complex_id = ?", (complex_id,)
    ).fetchone()
    rtokens = region_tokens(row["sigungu"], row["dong"]) if row else []
    filtered = filter_docs(docs, name=name, region_toks=rtokens, classifier=classifier)
    if not filtered:
        # fetch 성공·실 docs 전부 무관/오염 → 진짜 "후기 없음". 기존 청크 정화(always-delete-first·
        # 잔존 0). transient(위 가드)는 이미 NO_SOURCE 분기 → 여긴 데이터손실 위험 없음.
        lock_fn = lock or _null_lock
        with lock_fn() as acquired:  # type: ignore[operator]
            if acquired is False:
                return BuildResult(LOCK_YIELD, docs_fetched=len(docs))
            _delete_complex(conn, complex_id)
            conn.commit()
        return BuildResult(NO_RELEVANT, docs_fetched=len(docs))  # 빈 코퍼스·UI "후기 미수집"
    docs = filtered

    # 3) 청킹 — chunk마다 source_type/url + span_ref(인용정밀).
    pending: list[PendingChunk] = []
    for doc in docs:
        for ch in chunk_doc(doc):
            pending.append(PendingChunk(
                source_type=doc.source_type, source_url=doc.source_url,
                span_ref=ch.span_ref, text=ch.text,
            ))
            if len(pending) >= max_chunks:
                break
        if len(pending) >= max_chunks:
            break
    if not pending:
        return BuildResult(NO_SOURCE, docs_fetched=len(docs))

    # 4) embed(graceful: down → defer·write skip·캐시 유지·crash 0).
    try:
        embed_res = embed_client.embed([pc.text for pc in pending])
    except EmbedUnavailable:
        return BuildResult(EMBED_DEFERRED, docs_fetched=len(docs))

    # 5) write under C47 락(점유면 양보). embed 성공 후이므로 락 보유 짧음.
    lock = lock or _null_lock
    with lock() as acquired:  # type: ignore[operator]
        if acquired is False:
            return BuildResult(LOCK_YIELD, docs_fetched=len(docs))
        n = write_chunks(
            conn, complex_id, pending, embed_res.vectors, embed_res.recipe, now=now, ttl=ttl,
        )
    return BuildResult(BUILT, chunks_written=n, docs_fetched=len(docs))


def _recipe_of(client: Embedder):  # type: ignore[no-untyped-def]
    """클라 config → EmbedRecipe(호출 없이 신선도 비교용 — embed()가 박는 것과 동일)."""
    from app.embed.client import EMBED_DIM, EmbedRecipe

    return EmbedRecipe(embed_model=client.embed_model, dim=EMBED_DIM, normalized=True)
