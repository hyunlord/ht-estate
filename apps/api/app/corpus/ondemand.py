"""온디맨드 lazy 코퍼스 build (E3-2) — 후보 detail 트리거 시 단건만 백그라운드 build.

gym/pet OnDemandEnricher 동형: 신선(TTL+레시피) hit→READY 즉답 · miss→백그라운드 build+PENDING
(검색/카드 블록 0) · in-flight 디덥 · 음성 쿨다운(무소스/defer 재시도 storm 방지) · graceful
(fetcher 미구성→UNAVAILABLE·예외 삼킴). 백그라운드 write는 C47 락(ShlockBatch) 직렬화. retrieval/
rerank/종합은 E3-3 — 여긴 코퍼스 *쓰기* 트리거만. review_chunk/_vec만 write → 지문/counts 불변.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.corpus.builder import DEFAULT_TTL, build_corpus
from app.corpus.relevance import make_doc_classifier
from app.corpus.store import is_fresh
from app.embed.client import Embedder, embed_client_from_env
from app.enrich.fetcher import SourceFetcher
from app.enrich.ondemand import PENDING, READY, UNAVAILABLE
from app.enrich.provider import LLMProvider
from app.store.db import get_connection

NEGATIVE_COOLDOWN = timedelta(hours=6)


def _recipe_of(client: Embedder):  # type: ignore[no-untyped-def]
    from app.embed.client import EMBED_DIM, EmbedRecipe

    return EmbedRecipe(embed_model=client.embed_model, dim=EMBED_DIM, normalized=True)


@dataclass
class OnDemandCorpus:
    """후보-한정 lazy 코퍼스 트리거 — inflight 디덥 + 음성 쿨다운 + 백그라운드 build(C47 락)."""

    fetcher: SourceFetcher | None
    embed_client: Embedder = field(default_factory=embed_client_from_env)
    provider: LLMProvider | None = None  # gemma doc 분류기(bulk와 동형 정밀)·미주입이면 룰만
    db_path: str | None = None
    ttl: timedelta = DEFAULT_TTL
    negative_cooldown: timedelta = NEGATIVE_COOLDOWN
    lock: Callable[[], object] | None = None  # C47 ShlockBatch(미주입이면 무락 — 테스트)
    submit: Callable[[Callable[[], None]], object] | None = None
    conn_factory: Callable[[], sqlite3.Connection] | None = None
    _inflight: set[str] = field(default_factory=set)
    _attempted: dict[str, datetime] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _pool: ThreadPoolExecutor | None = field(default=None, repr=False)

    def _do_submit(self, fn: Callable[[], None]) -> None:
        if self.submit is not None:
            self.submit(fn)
            return
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=2)  # bounded(후보-한정)
        self._pool.submit(fn)

    def ensure(
        self, conn: sqlite3.Connection, complex_id: str, name: str, *, now: datetime | None = None
    ) -> str:
        """코퍼스 상태 → READY(신선) / PENDING(build 트리거·진행) / UNAVAILABLE(fetcher 미구성).

        신선하면 즉답 READY. miss면 단건 백그라운드 build 제출 후 PENDING(디덥·쿨다운). 동기 차단 0.
        """
        now = now or datetime.now(UTC)
        if is_fresh(conn, complex_id, _recipe_of(self.embed_client), now=now):
            return READY
        if self.fetcher is None:
            return UNAVAILABLE
        with self._lock:
            if complex_id in self._inflight:
                return PENDING
            att = self._attempted.get(complex_id)
            if att is not None and now - att < self.negative_cooldown:
                return PENDING  # 최근 build·무결과/defer → 쿨다운(재시도 storm 방지)
            self._inflight.add(complex_id)
        self._do_submit(lambda: self._run(complex_id, name))
        return PENDING

    def _run(self, complex_id: str, name: str) -> None:
        """백그라운드 단건 build — 자기 conn + C47 락. graceful(예외 삼킴·defer)."""
        try:
            if self.conn_factory is not None:
                conn = self.conn_factory()
            else:
                conn = get_connection(self.db_path) if self.db_path else get_connection()
            try:
                # 지역 read → gemma 분류기(bulk와 동형 정밀: 개발기사·타지 reject). 없으면 룰만.
                row = conn.execute(
                    "SELECT COALESCE(sigungu,'')||' '||COALESCE(dong,'') AS region "
                    "FROM complex WHERE complex_id = ?", (complex_id,)
                ).fetchone()
                region = (row["region"] or "").strip() if row else ""
                build_corpus(
                    conn, complex_id, name,
                    fetcher=self.fetcher,  # type: ignore[arg-type]
                    embed_client=self.embed_client,
                    now=datetime.now(UTC), ttl=self.ttl, lock=self.lock,
                    classifier=make_doc_classifier(self.provider, name, region),
                )
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 — graceful: build 실패는 defer(쿨다운 후 재시도·crash 0)
            pass
        finally:
            with self._lock:
                self._inflight.discard(complex_id)
                self._attempted[complex_id] = datetime.now(UTC)
