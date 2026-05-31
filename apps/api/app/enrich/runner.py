"""lazy read-through 오케스트레이션 (설계 §6).

후보 × 속성 → store 조회(TTL) → fresh면 캐시(추출기 미호출) / miss면 추출기 호출 →
write-back. 추출기 호출(웹/LLM I/O)만 병렬(동시 3~5 상한); store read/write는 메인
스레드에서 직렬(sqlite 동시쓰기 회피). 추출기는 주입형이라 키리스 테스트 가능.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Protocol

from app.enrich.store import EnrichmentFact, has_fresh, read_facts, write_facts

DEFAULT_CONCURRENCY = 4


class Extractor(Protocol):
    """주입형 추출기 — (complex_id, attribute) → 사실 리스트(출처별). 무결과면 빈 리스트.

    P1-1은 인터페이스 + stub만. 실 웹검색+LLM 추출기는 P1-2(속성별).
    """

    def __call__(self, complex_id: str, attribute: str, /) -> list[EnrichmentFact]: ...


def stub_extractor(complex_id: str, attribute: str) -> list[EnrichmentFact]:
    """no-op 추출기 — 항상 무결과. 실 추출기 부재 시 기본값(P1-2가 대체)."""
    return []


def enrich(
    conn: sqlite3.Connection,
    candidates: Sequence[str],
    attribute: str,
    extractor: Extractor,
    *,
    ttl: timedelta,
    now: datetime,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> dict[str, list[EnrichmentFact]]:
    """후보(complex_id) × 속성 → {complex_id: facts}. fresh는 캐시, miss는 추출+write-back.

    miss = TTL-유효 행 0개. 추출기는 병렬(concurrency 상한), write-back은 메인 직렬.
    추출 무결과면 write 안 함(다음 호출도 miss로 재시도 — TTL 음수 캐시는 P1-2에서 판단).
    """
    misses = [cid for cid in candidates if not has_fresh(conn, cid, attribute, now=now)]

    extracted: dict[str, list[EnrichmentFact]] = {}
    if misses:
        workers = max(1, min(concurrency, len(misses)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = pool.map(lambda cid: (cid, extractor(cid, attribute)), misses)
            extracted = dict(results)

    # write-back은 메인 스레드 직렬(sqlite 동시쓰기 회피)
    for cid, facts in extracted.items():
        if facts:
            write_facts(conn, cid, attribute, facts, ttl=ttl, now=now)

    # 캐시(fresh)와 신규(write-back) 통합 — store에서 최종 상태를 다시 읽어 일관성 보장
    return {cid: read_facts(conn, cid, attribute, now=now) for cid in candidates}
