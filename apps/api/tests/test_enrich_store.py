"""enrichment store — TTL read · 다중출처 · write-back provenance · 멱등."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from app.enrich.store import EnrichmentFact, has_fresh, read_facts, write_facts
from app.store.db import get_connection, init_db

NOW = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
TTL = timedelta(days=30)


def _db() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO complex (complex_id, name) VALUES ('C1', '역삼자이')")
    conn.commit()
    return conn


def _fact(url: str, value: str = '{"allowed": true}', conf: float = 0.6) -> EnrichmentFact:
    return EnrichmentFact(value=value, confidence=conf, source_type="cafe", source_url=url)


def test_write_then_read_with_provenance() -> None:
    conn = _db()
    n = write_facts(conn, "C1", "pet_allowed", [_fact("https://cafe/1")], ttl=TTL, now=NOW)
    assert n == 1
    facts = read_facts(conn, "C1", "pet_allowed", now=NOW)
    assert len(facts) == 1
    assert facts[0].source_url == "https://cafe/1"
    assert facts[0].confidence == 0.6
    assert facts[0].source_type == "cafe"
    # provenance 6필드 저장 확인
    row = conn.execute(
        "SELECT fetched_at, ttl_expires_at FROM enrichment WHERE source_url='https://cafe/1'"
    ).fetchone()
    assert row["fetched_at"] == NOW.isoformat()
    assert row["ttl_expires_at"] == (NOW + TTL).isoformat()


def test_multiple_sources_per_attribute() -> None:
    conn = _db()
    write_facts(
        conn,
        "C1",
        "pet_allowed",
        [_fact("https://cafe/1"), _fact("https://blog/2"), _fact("https://rule/3")],
        ttl=TTL,
        now=NOW,
    )
    facts = read_facts(conn, "C1", "pet_allowed", now=NOW)
    assert len(facts) == 3  # 출처당 1행
    assert [f.source_url for f in facts] == ["https://blog/2", "https://cafe/1", "https://rule/3"]


def test_ttl_expiry_boundary() -> None:
    conn = _db()
    write_facts(conn, "C1", "pet_allowed", [_fact("https://cafe/1")], ttl=TTL, now=NOW)
    # 만료 직전: 유효
    assert has_fresh(conn, "C1", "pet_allowed", now=NOW + TTL - timedelta(seconds=1)) is True
    # 만료 시점/이후: 무효(ttl_expires_at > now 아님)
    assert has_fresh(conn, "C1", "pet_allowed", now=NOW + TTL) is False
    assert read_facts(conn, "C1", "pet_allowed", now=NOW + TTL + timedelta(days=1)) == []


def test_write_is_idempotent_upsert() -> None:
    conn = _db()
    write_facts(conn, "C1", "pet_allowed", [_fact("https://cafe/1", conf=0.5)], ttl=TTL, now=NOW)
    # 같은 출처 재추출 → 갱신(행 1개 유지)
    later = NOW + timedelta(days=10)
    write_facts(conn, "C1", "pet_allowed", [_fact("https://cafe/1", conf=0.9)], ttl=TTL, now=later)
    facts = read_facts(conn, "C1", "pet_allowed", now=later)
    assert len(facts) == 1
    assert facts[0].confidence == 0.9  # 갱신됨
    row = conn.execute(
        "SELECT ttl_expires_at FROM enrichment WHERE source_url='https://cafe/1'"
    ).fetchone()
    assert row["ttl_expires_at"] == (later + TTL).isoformat()  # TTL 갱신


def test_has_fresh_false_when_empty() -> None:
    conn = _db()
    assert has_fresh(conn, "C1", "pet_allowed", now=NOW) is False
    assert read_facts(conn, "C1", "pet_allowed", now=NOW) == []
