"""lazy read-through 오케스트레이션 — hit/miss/다중출처/병렬/TTL(fake 추출기, 키리스)."""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta

from app.enrich.runner import enrich, stub_extractor
from app.enrich.store import EnrichmentFact, write_facts
from app.store.db import get_connection, init_db

NOW = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
TTL = timedelta(days=30)


def _db(*complex_ids: str) -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.executemany(
        "INSERT INTO complex (complex_id, name) VALUES (?, ?)",
        [(cid, cid) for cid in complex_ids],
    )
    conn.commit()
    return conn


def _fact(cid: str) -> EnrichmentFact:
    return EnrichmentFact(
        value='{"allowed": true}', confidence=0.6, source_type="cafe",
        source_url=f"https://cafe/{cid}",
    )


def test_miss_calls_extractor_and_writes_back() -> None:
    conn = _db("C1")
    calls: list[str] = []

    def extractor(cid: str, attribute: str) -> list[EnrichmentFact]:
        calls.append(cid)
        return [_fact(cid)]

    result = enrich(conn, ["C1"], "pet_allowed", extractor, ttl=TTL, now=NOW)
    assert calls == ["C1"]  # miss → 추출기 호출
    assert len(result["C1"]) == 1
    # write-back 확인: 두 번째 호출은 fresh → 추출기 미호출
    calls.clear()
    result2 = enrich(conn, ["C1"], "pet_allowed", extractor, ttl=TTL, now=NOW)
    assert calls == []  # hit → 캐시
    assert len(result2["C1"]) == 1


def test_fresh_hit_does_not_call_extractor() -> None:
    conn = _db("C1")
    write_facts(conn, "C1", "pet_allowed", [_fact("C1")], ttl=TTL, now=NOW)

    def extractor(cid: str, attribute: str) -> list[EnrichmentFact]:
        raise AssertionError("fresh hit인데 추출기 호출됨")

    result = enrich(conn, ["C1"], "pet_allowed", extractor, ttl=TTL, now=NOW)
    assert len(result["C1"]) == 1


def test_expired_triggers_re_extraction() -> None:
    conn = _db("C1")
    write_facts(conn, "C1", "pet_allowed", [_fact("C1")], ttl=TTL, now=NOW)
    calls: list[str] = []

    def extractor(cid: str, attribute: str) -> list[EnrichmentFact]:
        calls.append(cid)
        return [_fact(cid)]

    # TTL 만료 후 → miss → 재추출
    later = NOW + TTL + timedelta(days=1)
    enrich(conn, ["C1"], "pet_allowed", extractor, ttl=TTL, now=later)
    assert calls == ["C1"]


def test_multiple_sources_returned() -> None:
    conn = _db("C1")

    def extractor(cid: str, attribute: str) -> list[EnrichmentFact]:
        return [
            EnrichmentFact(value="{}", confidence=0.6, source_type="cafe", source_url="u1"),
            EnrichmentFact(value="{}", confidence=0.5, source_type="blog", source_url="u2"),
        ]

    result = enrich(conn, ["C1"], "pet_allowed", extractor, ttl=TTL, now=NOW)
    assert len(result["C1"]) == 2


def test_concurrency_cap_respected() -> None:
    conn = _db(*[f"C{i}" for i in range(10)])
    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def extractor(cid: str, attribute: str) -> list[EnrichmentFact]:
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        # 동시성 관찰을 위해 잠깐 점유
        for _ in range(10000):
            pass
        with lock:
            state["active"] -= 1
        return [_fact(cid)]

    cids = [f"C{i}" for i in range(10)]
    enrich(conn, cids, "pet_allowed", extractor, ttl=TTL, now=NOW, concurrency=3)
    assert state["peak"] <= 3  # 동시 상한 준수


def test_mixed_hit_and_miss() -> None:
    conn = _db("C1", "C2")
    write_facts(conn, "C1", "pet_allowed", [_fact("C1")], ttl=TTL, now=NOW)  # C1 fresh
    calls: list[str] = []

    def extractor(cid: str, attribute: str) -> list[EnrichmentFact]:
        calls.append(cid)
        return [_fact(cid)]

    result = enrich(conn, ["C1", "C2"], "pet_allowed", extractor, ttl=TTL, now=NOW)
    assert calls == ["C2"]  # C1은 hit, C2만 추출
    assert len(result["C1"]) == 1 and len(result["C2"]) == 1


def test_stub_extractor_returns_empty() -> None:
    conn = _db("C1")
    result = enrich(conn, ["C1"], "pet_allowed", stub_extractor, ttl=TTL, now=NOW)
    assert result["C1"] == []  # 무결과 → write 안 함
