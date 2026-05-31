"""gym 부착 — 합성(synthesize_gym) · read-through 부착(attach_gym) · 라우트 통합.

키리스: :memory: + seeded enrichment + stub_extractor(읽기 전용). 라이브 추출 없음.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.enrich.runner import stub_extractor
from app.enrich.store import EnrichmentFact, write_facts
from app.main import app, get_db
from app.search.gym import attach_gym, synthesize_gym
from app.search.repo import search_complexes
from app.search.spec import HardFilterSpec

NOW = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
TTL = timedelta(days=90)


def _gym_fact(
    has_gym: str, *, confidence: float, source_type: str, source_url: str, evidence: str = ""
) -> EnrichmentFact:
    return EnrichmentFact(
        value=json.dumps({"has_gym": has_gym, "evidence": evidence}, ensure_ascii=False),
        confidence=confidence,
        source_type=source_type,
        source_url=source_url,
    )


# ───────────────────────── synthesize_gym (순수 합성) ─────────────────────────


def test_synthesize_none_when_no_facts() -> None:
    summary = synthesize_gym([])
    assert summary.has_gym == "none"  # 미조사 — unknown(불명)과 구분
    assert summary.confidence is None
    assert summary.evidence is None
    assert summary.sources == []


def test_synthesize_single_source() -> None:
    fact = _gym_fact(
        "yes", confidence=0.9, source_type="official",
        source_url="https://the-h.co.kr/x", evidence="단지 내 피트니스",
    )
    summary = synthesize_gym([fact])
    assert summary.has_gym == "yes"
    assert summary.confidence == 0.9
    assert summary.evidence == "단지 내 피트니스"
    assert [s.source_url for s in summary.sources] == ["https://the-h.co.kr/x"]


def test_synthesize_multi_source_picks_highest_confidence_primary() -> None:
    # 충돌: no@0.6 vs yes@0.8 → primary=yes(최고 confidence), sources엔 둘 다 노출.
    low = _gym_fact("no", confidence=0.6, source_type="blog", source_url="https://b/1")
    high = _gym_fact("yes", confidence=0.8, source_type="official", source_url="https://o/2",
                     evidence="공식홈 피트니스")
    summary = synthesize_gym([low, high])
    assert summary.has_gym == "yes"
    assert summary.confidence == 0.8
    assert summary.evidence == "공식홈 피트니스"
    assert {s.source_url for s in summary.sources} == {"https://b/1", "https://o/2"}  # 다출처 전부


def test_synthesize_graceful_on_malformed_and_invalid() -> None:
    bad_json = EnrichmentFact(
        value="not-json", confidence=0.5, source_type="web", source_url="https://x/1"
    )
    invalid_state = _gym_fact("maybe", confidence=0.4, source_type="web", source_url="https://x/2")
    assert synthesize_gym([bad_json]).has_gym == "unknown"  # 파싱 실패 → 보수적 unknown
    assert synthesize_gym([invalid_state]).has_gym == "unknown"  # 도메인 밖 → unknown


# ───────────────────────── attach_gym (read-through, 읽기 전용) ─────────────────────────


def _seed(conn: sqlite3.Connection) -> None:
    """search_db 위에 gym enrichment 시드 — 검증 예시 매핑(C3 ✓·C2 ✗·C1 △·C4 none)."""
    write_facts(conn, "C3", "gym", [
        _gym_fact("yes", confidence=0.9, source_type="official",
                  source_url="https://the-h.co.kr/x", evidence="단지 내 피트니스(공식홈)"),
    ], ttl=TTL, now=NOW)
    write_facts(conn, "C2", "gym", [
        _gym_fact("no", confidence=0.6, source_type="agent_research",
                  source_url="urn:ht-estate:c4-agent:C2", evidence="인근 상업 헬스장뿐"),
    ], ttl=TTL, now=NOW)
    write_facts(conn, "C1", "gym", [
        _gym_fact("unknown", confidence=0.3, source_type="agent_research",
                  source_url="urn:ht-estate:c4-agent:C1", evidence="단지내 단정 불가"),
    ], ttl=TTL, now=NOW)
    # C4는 시드 없음 → none.


def test_attach_gym_maps_seeded_and_unseeded(search_db: sqlite3.Connection) -> None:
    _seed(search_db)
    candidates = search_complexes(search_db, HardFilterSpec())
    attach_gym(search_db, candidates, now=NOW, ttl=TTL)

    by_id = {c.complex_id: c for c in candidates}
    assert by_id["C3"].gym is not None and by_id["C3"].gym.has_gym == "yes"
    assert by_id["C2"].gym is not None and by_id["C2"].gym.has_gym == "no"
    assert by_id["C1"].gym is not None and by_id["C1"].gym.has_gym == "unknown"
    assert by_id["C4"].gym is not None and by_id["C4"].gym.has_gym == "none"  # 미시드
    # 출처 노출(provenance): C3는 http, C2는 urn sentinel.
    assert by_id["C3"].gym.sources[0].source_url.startswith("https://")
    assert by_id["C2"].gym.sources[0].source_url.startswith("urn:")


def test_attach_gym_is_read_only_on_miss(search_db: sqlite3.Connection) -> None:
    """stub read-through: miss(C1~C4)는 추출/write 0 — enrichment 테이블 불변."""
    before = search_db.execute("SELECT COUNT(*) AS n FROM enrichment").fetchone()["n"]
    candidates = search_complexes(search_db, HardFilterSpec())

    calls: list[str] = []

    def tracking_stub(cid: str, attribute: str) -> list[EnrichmentFact]:
        calls.append(cid)
        return stub_extractor(cid, attribute)  # 항상 [] — 읽기 전용

    attach_gym(search_db, candidates, now=NOW, ttl=TTL, extractor=tracking_stub)
    after = search_db.execute("SELECT COUNT(*) AS n FROM enrichment").fetchone()["n"]

    assert before == 0
    assert after == 0  # miss여도 write-back 0 → DB 불변(읽기 전용)
    assert sorted(calls) == ["C1", "C2", "C3", "C4"]  # miss마다 stub 호출되나 무결과
    assert all(c.gym is not None and c.gym.has_gym == "none" for c in candidates)


def test_attach_gym_empty_candidates_noop(search_db: sqlite3.Connection) -> None:
    attach_gym(search_db, [], now=NOW, ttl=TTL)  # 후보 0 → no-op(에러 없음)


# ───────────────────────── 라우트 통합 (TestClient) ─────────────────────────


@pytest.fixture
def client(search_db: sqlite3.Connection) -> Iterator[TestClient]:
    # 라우트는 실시간 now를 쓰므로 시드 TTL을 실 now 기준으로 fresh하게 둔다.
    write_facts(search_db, "C3", "gym", [
        _gym_fact("yes", confidence=0.9, source_type="official",
                  source_url="https://the-h.co.kr/x", evidence="단지 내 피트니스"),
    ], ttl=TTL, now=datetime.now(UTC))

    def _override() -> Iterator[sqlite3.Connection]:
        yield search_db

    app.dependency_overrides[get_db] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_route_attaches_gym(client: TestClient) -> None:
    resp = client.post("/complexes/search", json={})
    assert resp.status_code == 200
    by_id = {c["complex_id"]: c for c in resp.json()}
    # 시드 후보 → gym 부착
    assert by_id["C3"]["gym"]["has_gym"] == "yes"
    assert by_id["C3"]["gym"]["confidence"] == 0.9
    assert by_id["C3"]["gym"]["sources"][0]["source_url"] == "https://the-h.co.kr/x"
    # 미시드 후보 → none
    assert by_id["C1"]["gym"]["has_gym"] == "none"
    assert by_id["C1"]["gym"]["sources"] == []


def test_route_gym_does_not_affect_hard_filter(client: TestClient) -> None:
    # R1 회귀 가드: gym 부착은 후보 집합/필터를 바꾸지 않는다.
    resp = client.post("/complexes/search", json={"parking_ratio_gte": 1.3})
    assert {c["complex_id"] for c in resp.json()} == {"C1", "C3"}  # T0-6과 동일
