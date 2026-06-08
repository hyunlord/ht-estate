"""온디맨드 lazy 추출 오케스트레이션 (ux-1) — 디테일뷰 진입 시 단건만 백그라운드 추출.

검색/마커는 캐시 읽기 그대로(동기 차단 0). 상세 진입 시 이 모듈이 (단지, 속성)을:
  · **캐시 hit**(TTL-유효 사실) → 즉답(ready).
  · **miss** → 그 **단건**만 백그라운드 추출(라이브 extractor=Naver+Gemma) → 즉시 **pending** 반환
    (카드가 22–60s 블록 금지). 다음 폴링에서 완료분 픽업.
  · **in-flight 디덥** — 동일 (단지,속성) 동시요청은 추가 추출 안 함(pending만).
  · **음성 쿨다운** — 추출했으나 무결과면 일정시간 재추출 안 함(폴링 storm·반복 추출 방지).
  · **graceful** — provider 미구성 → unavailable. 추출 예외 → 삼킴(defer, crash/hang 금지).

**후보-한정**(요청당 1건) · enrichment 테이블만 write → 지문·건물/거래 수 불변(구조적).
키리스: provider·fetcher·submit·conn_factory 주입형(테스트는 mock·inline).
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.enrich.fetcher import SourceFetcher
from app.enrich.live import live_extractors
from app.enrich.provider import LLMProvider
from app.enrich.runner import enrich
from app.enrich.store import EnrichmentFact, read_facts
from app.store.db import get_connection

# 추출했으나 무결과(defer)일 때 재추출 안 하는 인메모리 쿨다운 — 폴링/반복 진입 storm 방지.
# (재시작 시 리셋 → 자연 재시도. 양성 결과는 enrichment TTL이 캐시하므로 무관.)
NEGATIVE_COOLDOWN = timedelta(hours=6)

# 응답 상태 — ready(캐시값 합성 가능) / pending(추출 진행·폴링) / unavailable(provider 미구성).
READY = "ready"
PENDING = "pending"
UNAVAILABLE = "unavailable"


@dataclass
class OnDemandEnricher:
    """단건 온디맨드 추출 상태머신 — inflight 디덥 + 음성 쿨다운 + 백그라운드 제출.

    submit/conn_factory 주입 가능(테스트는 inline·temp-db). 기본은 자체 ThreadPoolExecutor(2,
    후보-한정 bounded)와 get_connection(요청 conn과 별개로 백그라운드 스레드가 자기 conn 사용).
    """

    provider: LLMProvider | None
    fetcher: SourceFetcher
    db_path: str | None = None
    ttl: timedelta = timedelta(days=90)
    negative_cooldown: timedelta = NEGATIVE_COOLDOWN
    submit: Callable[[Callable[[], None]], object] | None = None
    conn_factory: Callable[[], sqlite3.Connection] | None = None
    _inflight: set[tuple[str, str]] = field(default_factory=set)
    _attempted: dict[tuple[str, str], datetime] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _pool: ThreadPoolExecutor | None = field(default=None, repr=False)

    def _do_submit(self, fn: Callable[[], None]) -> None:
        if self.submit is not None:
            self.submit(fn)
            return
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=2)  # bounded(후보-한정)
        self._pool.submit(fn)

    def status(
        self,
        conn: sqlite3.Connection,
        complex_id: str,
        attribute: str,
        *,
        alias: tuple[str, ...] = (),
        now: datetime | None = None,
    ) -> tuple[str, list[EnrichmentFact]]:
        """(state, facts) 반환. facts는 캐시(read-only, 별칭 폴백 포함). 호출부가 synthesize."""
        now = now or datetime.now(UTC)
        facts = read_facts(conn, complex_id, attribute, now=now)
        if not facts:
            for a in alias:
                facts = read_facts(conn, complex_id, a, now=now)
                if facts:
                    break
        if facts:
            return READY, facts
        if self.provider is None:
            return UNAVAILABLE, []  # 라이브 미구성 → 추출 불가(카드: 정보 없음)
        key = (complex_id, attribute)
        with self._lock:
            if key in self._inflight:
                return PENDING, []  # 이미 추출 중(디덥)
            att = self._attempted.get(key)
            if att is not None and now - att < self.negative_cooldown:
                return READY, []  # 최근 추출·무결과 → 정보 없음(재추출 안 함)
            self._inflight.add(key)
        self._do_submit(lambda: self._run(complex_id, attribute))
        return PENDING, []

    def _run(self, complex_id: str, attribute: str) -> None:
        """백그라운드 단건 추출 — 자기 conn에서 라이브 extractor로 enrich(write-back). graceful."""
        try:
            if self.conn_factory is not None:
                conn = self.conn_factory()
            else:
                conn = get_connection(self.db_path) if self.db_path else get_connection()
            try:
                exts = live_extractors(
                    conn, [complex_id], provider=self.provider, fetcher=self.fetcher
                )
                if exts and attribute in exts:
                    enrich(
                        conn, [complex_id], attribute, exts[attribute],
                        ttl=self.ttl, now=datetime.now(UTC),
                    )
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 — graceful: 추출 실패는 defer(다음 요청 쿨다운 후 재시도)
            pass
        finally:
            with self._lock:
                self._inflight.discard((complex_id, attribute))
                self._attempted[(complex_id, attribute)] = datetime.now(UTC)
