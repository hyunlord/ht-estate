"""pet 부착 — 합성(synthesize_pet) · read-through 부착(attach_pet) · 라우트 통합(gym 공존).

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
from app.search.pet import attach_pet, synthesize_pet
from app.search.repo import search_complexes
from app.search.spec import HardFilterSpec

NOW = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
TTL = timedelta(days=90)


def _pet_fact(
    pet: str, *, confidence: float, source_type: str, source_url: str,
    evidence: str = "", caveats: list[str] | None = None, confirm: bool = True,
) -> EnrichmentFact:
    return EnrichmentFact(
        value=json.dumps(
            {"pet_allowed": pet, "evidence": evidence, "caveats": caveats or [],
             "confirm_with_office": confirm},
            ensure_ascii=False,
        ),
        confidence=confidence,
        source_type=source_type,
        source_url=source_url,
    )


# ───────────────────────── synthesize_pet (순수 합성) ─────────────────────────


def test_synthesize_none_when_no_facts() -> None:
    s = synthesize_pet([])
    assert s.pet_allowed == "none"
    assert s.confidence is None
    assert s.evidence is None
    assert s.caveats == []
    assert s.confirm_with_office is True  # 미조사여도 확인 권고는 유지
    assert s.sources == []


def test_synthesize_conditional_preserves_caveats_and_confirm() -> None:
    fact = _pet_fact(
        "conditional", confidence=0.55, source_type="news", source_url="https://mk/1",
        evidence="입주민 인식표·외부 출입 제한", caveats=["견종 제한", "마릿수 제한"], confirm=True,
    )
    s = synthesize_pet([fact])
    assert s.pet_allowed == "conditional"
    assert s.confidence == 0.55
    assert s.caveats == ["견종 제한", "마릿수 제한"]  # 제한 단서 보존
    assert s.confirm_with_office is True


def test_synthesize_multi_source_highest_confidence_primary() -> None:
    low = _pet_fact("unknown", confidence=0.2, source_type="agent_research", source_url="urn:x:1")
    high = _pet_fact("conditional", confidence=0.6, source_type="official",
                     source_url="https://o/2", caveats=["무게 10kg 이하"])
    s = synthesize_pet([low, high])
    assert s.pet_allowed == "conditional"  # 최고 confidence가 primary
    assert s.caveats == ["무게 10kg 이하"]
    assert {src.source_url for src in s.sources} == {"urn:x:1", "https://o/2"}  # 다출처 전부


def test_synthesize_graceful_on_malformed_and_invalid() -> None:
    bad = EnrichmentFact(value="not-json", confidence=0.5, source_type="web",
                         source_url="https://x/1")
    invalid = _pet_fact("maybe", confidence=0.4, source_type="web", source_url="https://x/2")
    assert synthesize_pet([bad]).pet_allowed == "unknown"  # 파싱 실패 → 보수적
    assert synthesize_pet([bad]).confirm_with_office is True  # 누락 시 보수적 true
    assert synthesize_pet([invalid]).pet_allowed == "unknown"  # 도메인 밖 → unknown


# ───────────────────────── attach_pet (read-through, 읽기 전용) ─────────────────────────


def _seed(conn: sqlite3.Connection) -> None:
    write_facts(conn, "C3", "pet_allowed", [
        _pet_fact("conditional", confidence=0.55, source_type="news",
                  source_url="https://www.mk.co.kr/x", evidence="인식표·외부 출입 제한",
                  caveats=["입주민 인식표 의무", "외부 반려동물 출입 제한"]),
    ], ttl=TTL, now=NOW)
    write_facts(conn, "C2", "pet_allowed", [
        _pet_fact("unknown", confidence=0.2, source_type="agent_research",
                  source_url="urn:ht-estate:c5-agent:C2", evidence="공개 신호 없음"),
    ], ttl=TTL, now=NOW)
    # C1·C4는 시드 없음 → none.


def test_attach_pet_maps_seeded_and_unseeded(search_db: sqlite3.Connection) -> None:
    _seed(search_db)
    candidates = search_complexes(search_db, HardFilterSpec())
    attach_pet(search_db, candidates, now=NOW, ttl=TTL)

    by_id = {c.complex_id: c for c in candidates}
    assert by_id["C3"].pet is not None and by_id["C3"].pet.pet_allowed == "conditional"
    assert by_id["C3"].pet.caveats == ["입주민 인식표 의무", "외부 반려동물 출입 제한"]
    assert by_id["C3"].pet.confirm_with_office is True
    assert by_id["C2"].pet is not None and by_id["C2"].pet.pet_allowed == "unknown"
    assert by_id["C1"].pet is not None and by_id["C1"].pet.pet_allowed == "none"  # 미시드
    assert by_id["C1"].pet.confirm_with_office is True  # none도 확인 권고


def test_attach_pet_is_read_only_on_miss(search_db: sqlite3.Connection) -> None:
    """stub read-through: miss는 추출/write 0 — enrichment 테이블 불변."""
    before = search_db.execute("SELECT COUNT(*) AS n FROM enrichment").fetchone()["n"]
    candidates = search_complexes(search_db, HardFilterSpec())

    calls: list[str] = []

    def tracking_stub(cid: str, attribute: str) -> list[EnrichmentFact]:
        calls.append(cid)
        return stub_extractor(cid, attribute)

    attach_pet(search_db, candidates, now=NOW, ttl=TTL, extractor=tracking_stub)
    after = search_db.execute("SELECT COUNT(*) AS n FROM enrichment").fetchone()["n"]

    assert before == 0
    assert after == 0  # miss여도 write-back 0 → DB 불변(읽기 전용)
    assert sorted(calls) == ["C1", "C2", "C3", "C4"]
    assert all(c.pet is not None and c.pet.pet_allowed == "none" for c in candidates)


def test_attach_pet_empty_candidates_noop(search_db: sqlite3.Connection) -> None:
    attach_pet(search_db, [], now=NOW, ttl=TTL)  # 후보 0 → no-op


# ──────────────── 읽기-타임 별칭(pet 정식 우선·pet_allowed 폴백, ux-1) ────────────────


def test_attach_pet_reads_legacy_alias(search_db: sqlite3.Connection) -> None:
    # 레거시 'pet_allowed'만 있어도 정식 'pet' 읽기가 별칭 폴백으로 본다(마이그레이션 0).
    write_facts(search_db, "C3", "pet_allowed", [
        _pet_fact("conditional", confidence=0.55, source_type="news", source_url="https://mk/x",
                  caveats=["소형견만"]),
    ], ttl=TTL, now=NOW)
    candidates = search_complexes(search_db, HardFilterSpec())
    attach_pet(search_db, candidates, now=NOW, ttl=TTL)
    by_id = {c.complex_id: c for c in candidates}
    assert by_id["C3"].pet is not None and by_id["C3"].pet.pet_allowed == "conditional"
    assert by_id["C3"].pet.caveats == ["소형견만"]


def test_attach_pet_prefers_fresh_canonical_over_legacy(search_db: sqlite3.Connection) -> None:
    # 정식 'pet'(라이브)과 레거시 'pet_allowed'가 둘 다 있으면 **정식이 우선**.
    write_facts(search_db, "C3", "pet_allowed", [
        _pet_fact("no", confidence=0.9, source_type="blog", source_url="https://old/x"),
    ], ttl=TTL, now=NOW)
    write_facts(search_db, "C3", "pet", [
        _pet_fact("conditional", confidence=0.6, source_type="cafe", source_url="https://new/x",
                  caveats=["무게 제한"]),
    ], ttl=TTL, now=NOW)
    candidates = search_complexes(search_db, HardFilterSpec())
    attach_pet(search_db, candidates, now=NOW, ttl=TTL)
    by_id = {c.complex_id: c for c in candidates}
    assert by_id["C3"].pet is not None
    assert by_id["C3"].pet.pet_allowed == "conditional"  # 정식 'pet' 우선(레거시 'no' 무시)
    assert {s.source_url for s in by_id["C3"].pet.sources} == {"https://new/x"}  # 정식 출처만


# ───────────────────────── 라우트 통합 (gym 공존) ─────────────────────────


@pytest.fixture
def client(search_db: sqlite3.Connection) -> Iterator[TestClient]:
    now = datetime.now(UTC)  # 라우트는 실시간 now → 시드를 fresh하게
    write_facts(search_db, "C3", "pet_allowed", [
        _pet_fact("conditional", confidence=0.55, source_type="news",
                  source_url="https://www.mk.co.kr/x", evidence="인식표·외부 출입 제한",
                  caveats=["입주민 인식표 의무"]),
    ], ttl=TTL, now=now)
    write_facts(search_db, "C3", "gym", [
        EnrichmentFact(value=json.dumps({"has_gym": "yes", "evidence": "단지 내 피트니스"}),
                       confidence=0.9, source_type="official", source_url="https://the-h/x"),
    ], ttl=TTL, now=now)

    def _override() -> Iterator[sqlite3.Connection]:
        yield search_db

    app.dependency_overrides[get_db] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_route_attaches_pet_and_gym_coexist(client: TestClient) -> None:
    resp = client.post("/complexes/search", json={})
    assert resp.status_code == 200
    by_id = {c["complex_id"]: c for c in resp.json()}
    # pet 부착
    assert by_id["C3"]["pet"]["pet_allowed"] == "conditional"
    assert by_id["C3"]["pet"]["caveats"] == ["입주민 인식표 의무"]
    assert by_id["C3"]["pet"]["confirm_with_office"] is True
    # gym 공존(회귀 0)
    assert by_id["C3"]["gym"]["has_gym"] == "yes"
    # 미시드 → none
    assert by_id["C1"]["pet"]["pet_allowed"] == "none"


def test_route_pet_does_not_affect_hard_filter(client: TestClient) -> None:
    # R1 회귀 가드: pet 부착은 후보 집합/필터를 바꾸지 않는다(T0-6과 동일).
    resp = client.post("/complexes/search", json={"parking_ratio_gte": 1.3})
    assert {c["complex_id"] for c in resp.json()} == {"C1", "C3"}
