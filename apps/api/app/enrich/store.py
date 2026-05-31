"""enrichment store — (complex_id, attribute)의 TTL-유효 사실 read + write-back.

provenance(원칙3): 사실은 (value · confidence · source_type · source_url · fetched_at ·
ttl_expires_at)를 들고 다닌다. 속성당 **출처별 다중 행**(§4 — 한 속성에 카페·블로그·규약 등).
시간은 `now`를 주입받아 TTL 판정을 결정론으로 한다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from pydantic import BaseModel


class EnrichmentFact(BaseModel):
    """한 출처의 사실. value는 JSON 직렬화 문자열(스키마 §4)."""

    value: str
    confidence: float
    source_type: str  # 'web'|'youtube'|'cafe'|'blog'|'kapt' ...
    source_url: str


def read_facts(
    conn: sqlite3.Connection,
    complex_id: str,
    attribute: str,
    *,
    now: datetime,
) -> list[EnrichmentFact]:
    """(complex_id, attribute)의 **TTL-유효(ttl_expires_at > now)** 행들. 만료/없음은 제외.

    속성당 여러 출처 = 여러 행을 그대로 반환(source_url 순 정렬로 결정론).
    """
    rows = conn.execute(
        "SELECT value, confidence, source_type, source_url FROM enrichment "
        "WHERE complex_id = ? AND attribute = ? AND ttl_expires_at > ? "
        "ORDER BY source_url",
        (complex_id, attribute, now.isoformat()),
    ).fetchall()
    return [
        EnrichmentFact(
            value=r["value"],
            confidence=r["confidence"],
            source_type=r["source_type"],
            source_url=r["source_url"],
        )
        for r in rows
    ]


def has_fresh(
    conn: sqlite3.Connection,
    complex_id: str,
    attribute: str,
    *,
    now: datetime,
) -> bool:
    """TTL-유효 행이 하나라도 있나(=hit). 없으면 miss(행 없음 또는 전부 만료)."""
    row = conn.execute(
        "SELECT 1 FROM enrichment "
        "WHERE complex_id = ? AND attribute = ? AND ttl_expires_at > ? LIMIT 1",
        (complex_id, attribute, now.isoformat()),
    ).fetchone()
    return row is not None


def write_facts(
    conn: sqlite3.Connection,
    complex_id: str,
    attribute: str,
    facts: list[EnrichmentFact],
    *,
    ttl: timedelta,
    now: datetime,
) -> int:
    """사실들을 write-back. PK(complex_id, attribute, source_url) 충돌 시 upsert(멱등).

    fetched_at=now, ttl_expires_at=now+ttl. 쓴 행 수 반환.
    """
    fetched_at = now.isoformat()
    expires_at = (now + ttl).isoformat()
    for fact in facts:
        conn.execute(
            "INSERT INTO enrichment "
            "(complex_id, attribute, value, confidence, source_type, source_url, "
            " fetched_at, ttl_expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(complex_id, attribute, source_url) DO UPDATE SET "
            "value=excluded.value, confidence=excluded.confidence, "
            "source_type=excluded.source_type, fetched_at=excluded.fetched_at, "
            "ttl_expires_at=excluded.ttl_expires_at",
            (
                complex_id,
                attribute,
                fact.value,
                fact.confidence,
                fact.source_type,
                fact.source_url,
                fetched_at,
                expires_at,
            ),
        )
    conn.commit()
    return len(facts)
