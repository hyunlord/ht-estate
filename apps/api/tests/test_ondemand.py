"""온디맨드 추출기 (ux-1) — cache-hit 즉답·miss→백그라운드+pending·디덥·graceful·음성 쿨다운.

키리스: provider·fetcher mock·submit 주입(라이브 호출 0). 백그라운드는 inline submit로 결정론.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from app.enrich.fetcher import NullFetcher, SourceDoc
from app.enrich.ondemand import PENDING, READY, UNAVAILABLE, OnDemandEnricher
from app.enrich.provider import ProviderError
from app.enrich.store import EnrichmentFact, read_facts, write_facts
from app.store.db import get_connection, init_db

NOW = datetime(2026, 6, 9, tzinfo=UTC)
TTL = timedelta(days=90)


class FakeProvider:
    def __init__(self, raw: str) -> None:
        self.raw = raw

    def complete(self, system: str, user: str, /) -> str:
        return self.raw


class DownProvider:
    def complete(self, system: str, user: str, /) -> str:
        raise ProviderError("down")


class FakeFetcher:
    def __init__(self, docs: list[SourceDoc]) -> None:
        self.docs = docs

    def fetch(self, query: str, *, kind: str) -> list[SourceDoc]:
        return list(self.docs)


def _conn_with_complex() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.execute(
        "INSERT INTO complex (complex_id, name, property_type) "
        "VALUES ('C1', '역삼자이', 'officetel')"
    )
    conn.commit()
    return conn


def _gym_fact() -> EnrichmentFact:
    return EnrichmentFact(
        value=json.dumps({"has_gym": "yes", "evidence": "피트니스"}),
        confidence=0.9, source_type="blog", source_url="http://a",
    )


def _record_submit() -> tuple[Callable, list]:
    calls: list = []
    return (lambda fn: calls.append(fn)), calls  # 제출만 기록(실행 안 함)


# ── cache-hit ──
def test_cache_hit_returns_ready_without_submit() -> None:
    conn = _conn_with_complex()
    write_facts(conn, "C1", "gym", [_gym_fact()], ttl=TTL, now=NOW)
    submit, calls = _record_submit()
    enr = OnDemandEnricher(provider=FakeProvider("[]"), fetcher=NullFetcher(), submit=submit)
    state, facts = enr.status(conn, "C1", "gym", now=NOW)
    assert state == READY and len(facts) == 1
    assert calls == []  # 캐시 hit → 추출 제출 0


# ── miss → pending + 백그라운드 제출 ──
def test_miss_submits_background_and_returns_pending() -> None:
    conn = _conn_with_complex()
    submit, calls = _record_submit()
    enr = OnDemandEnricher(provider=FakeProvider("[]"), fetcher=NullFetcher(), submit=submit)
    state, facts = enr.status(conn, "C1", "gym", now=NOW)
    assert state == PENDING and facts == []
    assert len(calls) == 1  # 단건 백그라운드 추출 제출
    assert ("C1", "gym") in enr._inflight


# ── 디덥 ──
def test_inflight_dedup_no_second_submit() -> None:
    conn = _conn_with_complex()
    submit, calls = _record_submit()
    enr = OnDemandEnricher(provider=FakeProvider("[]"), fetcher=NullFetcher(), submit=submit)
    enr.status(conn, "C1", "gym", now=NOW)
    state, _ = enr.status(conn, "C1", "gym", now=NOW)  # 진행 중 재요청
    assert state == PENDING
    assert len(calls) == 1  # 디덥 — 추가 제출 0


# ── provider 미구성 → unavailable ──
def test_unavailable_when_no_provider() -> None:
    conn = _conn_with_complex()
    submit, calls = _record_submit()
    enr = OnDemandEnricher(provider=None, fetcher=NullFetcher(), submit=submit)
    state, facts = enr.status(conn, "C1", "gym", now=NOW)
    assert state == UNAVAILABLE and facts == []
    assert calls == []  # 추출 불가 — 제출 0


# ── 음성 쿨다운(추출했으나 무결과 → 재추출 안 함) ──
def test_negative_cooldown_no_resubmit() -> None:
    conn = _conn_with_complex()
    submit, calls = _record_submit()
    enr = OnDemandEnricher(provider=FakeProvider("[]"), fetcher=NullFetcher(), submit=submit)
    enr._attempted[("C1", "gym")] = NOW  # 방금 추출·무결과로 표시
    state, facts = enr.status(conn, "C1", "gym", now=NOW + timedelta(minutes=5))
    assert state == READY and facts == []  # 정보 없음(재추출 안 함)
    assert calls == []


# ── 백그라운드 _run 이 실제로 write-back (inline submit + temp db) ──
def test_background_run_writes_facts(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db_file = str(tmp_path / "t.db")
    seed = get_connection(db_file)
    init_db(seed)
    seed.execute(
        "INSERT INTO complex (complex_id, name, property_type) VALUES ('C1','역삼자이','officetel')"
    )
    seed.commit()

    raw = json.dumps([{"source_url": "http://a", "has_gym": "yes", "confidence": 0.9}])
    docs = [SourceDoc(source_type="blog", source_url="http://a", text="피트니스 있음")]
    enr = OnDemandEnricher(
        provider=FakeProvider(raw), fetcher=FakeFetcher(docs),
        conn_factory=lambda: get_connection(db_file), submit=lambda fn: fn(),  # inline
    )
    state, _ = enr.status(seed, "C1", "gym", now=NOW)
    assert state == PENDING  # 제출됐고 inline이라 즉시 실행됨
    facts = read_facts(seed, "C1", "gym", now=NOW)
    assert len(facts) == 1 and json.loads(facts[0].value)["has_gym"] == "yes"
    assert ("C1", "gym") not in enr._inflight  # 완료 후 inflight 해제


# ── graceful: provider 다운 → crash 없이 defer(attempted 기록) ──
def test_background_run_graceful_on_provider_error(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db_file = str(tmp_path / "t.db")
    seed = get_connection(db_file)
    init_db(seed)
    seed.execute(
        "INSERT INTO complex (complex_id, name, property_type) VALUES ('C1','역삼자이','officetel')"
    )
    seed.commit()
    docs = [SourceDoc(source_type="blog", source_url="http://a", text="x")]
    enr = OnDemandEnricher(
        provider=DownProvider(), fetcher=FakeFetcher(docs),
        conn_factory=lambda: get_connection(db_file), submit=lambda fn: fn(),
    )
    state, _ = enr.status(seed, "C1", "gym", now=NOW)  # crash 없어야
    assert state == PENDING
    assert read_facts(seed, "C1", "gym", now=NOW) == []  # 무결과(defer)
    assert ("C1", "gym") in enr._attempted  # 시도 기록 → 쿨다운


# ── 라우트 통합 (GET /complexes/{id}/enrichment) ──
from collections.abc import Iterator  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app, get_db, get_enricher  # noqa: E402


def _client(conn: sqlite3.Connection, enr: OnDemandEnricher) -> TestClient:
    def _db() -> Iterator[sqlite3.Connection]:
        yield conn

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_enricher] = lambda: enr
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def test_route_404_unknown_complex() -> None:
    conn = _conn_with_complex()
    enr = OnDemandEnricher(
        provider=FakeProvider("[]"), fetcher=NullFetcher(), submit=lambda fn: None
    )
    resp = _client(conn, enr).get("/complexes/NOPE/enrichment")
    assert resp.status_code == 404


def test_route_cache_hit_ready_summary() -> None:
    conn = _conn_with_complex()
    write_facts(conn, "C1", "gym", [_gym_fact()], ttl=TTL, now=datetime.now(UTC))
    enr = OnDemandEnricher(
        provider=FakeProvider("[]"), fetcher=NullFetcher(), submit=lambda fn: None
    )
    resp = _client(conn, enr).get("/complexes/C1/enrichment")
    assert resp.status_code == 200
    body = resp.json()
    assert body["gym"]["status"] == "ready"
    assert body["gym"]["summary"]["has_gym"] == "yes"
    assert body["pet"]["status"] in {"pending", "unavailable", "ready"}


def test_route_miss_returns_pending() -> None:
    conn = _conn_with_complex()
    calls: list = []
    enr = OnDemandEnricher(
        provider=FakeProvider("[]"), fetcher=NullFetcher(), submit=lambda fn: calls.append(fn)
    )
    resp = _client(conn, enr).get("/complexes/C1/enrichment")
    assert resp.status_code == 200
    body = resp.json()
    assert body["gym"]["status"] == "pending" and body["gym"]["summary"] is None
    assert len(calls) >= 1  # 백그라운드 추출 제출(gym/pet)


def test_route_pet_alias_cache_hit_from_legacy() -> None:
    # 레거시 pet_allowed만 있어도 라우트가 ready로 합성(별칭 폴백)
    conn = _conn_with_complex()
    fact = EnrichmentFact(
        value=json.dumps({"pet_allowed": "conditional", "evidence": "규약", "caveats": ["소형견"],
                          "confirm_with_office": True}),
        confidence=0.6, source_type="cafe", source_url="http://p",
    )
    write_facts(conn, "C1", "pet_allowed", [fact], ttl=TTL, now=datetime.now(UTC))
    enr = OnDemandEnricher(
        provider=FakeProvider("[]"), fetcher=NullFetcher(), submit=lambda fn: None
    )
    resp = _client(conn, enr).get("/complexes/C1/enrichment")
    body = resp.json()
    assert body["pet"]["status"] == "ready"
    assert body["pet"]["summary"]["pet_allowed"] == "conditional"
    assert body["pet"]["summary"]["confirm_with_office"] is True
