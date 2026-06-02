"""soft 랭킹 — 정렬·required>preferred·demote-not-exclude·SET 불변·none 불변 (키리스).

랭킹 입력(Candidate.gym/pet)은 직접 구성(부착 로직과 직교 — 순수 정렬 단위 테스트).
라우트 통합은 :memory: seeded + stub로 SET 불변·none 불변 검증.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.enrich.store import EnrichmentFact, write_facts
from app.main import app, get_db
from app.search.gym import GymSummary
from app.search.pet import PetSummary
from app.search.ranking import rank_candidates
from app.search.repo import Candidate
from app.search.spec import SoftSpec


def _pet_fact(pet: str, *, confidence: float, source_type: str, source_url: str) -> EnrichmentFact:
    return EnrichmentFact(
        value=json.dumps({"pet_allowed": pet, "evidence": "", "caveats": [],
                          "confirm_with_office": True}),
        confidence=confidence, source_type=source_type, source_url=source_url,
    )


def _cand(cid: str, *, gym: str | None = None, gym_conf: float = 0.8,
          pet: str | None = None, pet_conf: float = 0.8) -> Candidate:
    """랭킹 입력 후보 — gym/pet summary만 채운다(정렬에 쓰이는 필드)."""
    return Candidate(
        complex_id=cid, name=cid, approval_date=None, parking_ratio=None,
        parking_underground=None, household_count=None, lat=None, lng=None,
        source_url=None, transaction_count=0, price_min=None, price_max=None,
        representative_trade=None,
        gym=GymSummary(has_gym=gym, confidence=gym_conf, evidence=None, sources=[])
        if gym is not None else GymSummary(has_gym="none", confidence=None, evidence=None,
                                           sources=[]),
        pet=PetSummary(pet_allowed=pet, confidence=pet_conf, evidence=None, caveats=[],
                       confirm_with_office=True, sources=[])
        if pet is not None else PetSummary(pet_allowed="none", confidence=None, evidence=None,
                                           caveats=[], confirm_with_office=True, sources=[]),
    )


def _ids(cands: list[Candidate]) -> list[str]:
    return [c.complex_id for c in cands]


# ───────────────────────── review 랭킹 불변 (P3-1) ─────────────────────────


def test_review_is_never_a_ranking_signal() -> None:
    # 후기는 표시 전용 — soft 점수에 안 들어간다. 같은 gym/pet에 review만 달라도 순서 불변.
    from app.search.enrichment import EnrichSource
    from app.search.review import ReviewSummary

    high = _cand("A", gym="yes", gym_conf=0.9)
    high.review = ReviewSummary(
        summary="극찬 후기", points=["조용"], confidence=0.5,
        sources=[EnrichSource(source_type="youtube", source_url="https://y/1")],
    )
    low = _cand("B", gym="yes", gym_conf=0.9)  # 동일 gym, review 없음
    # gym 동점(둘 다 yes·0.9) → review가 신호면 A가 위로. 안정정렬로 입력순(A,B) 유지여야.
    ranked = rank_candidates([high, low], SoftSpec(gym="required"))
    assert _ids(ranked) == ["A", "B"]  # review 무관 — 동점 안정정렬(입력순)
    # 역순 입력이면 역순 유지(= review가 순서를 못 바꾼다)
    ranked_rev = rank_candidates([low, high], SoftSpec(gym="required"))
    assert _ids(ranked_rev) == ["B", "A"]


def test_review_is_not_a_soft_criterion() -> None:
    # 구조적 보장(P4-2a 일반화 후): review는 조건 레지스트리 밖 → soft 랭킹 신호 불가(표시 전용).
    # (구 SoftSpec=={gym,pet} 체크는 일반화로 폐기 — 대신 레지스트리 + demote-not-exclude로 검증.)
    from app.search.criteria import REGISTRY

    assert "review" not in REGISTRY and "review_summary" not in REGISTRY
    assert {"gym", "pet", "criteria"} <= set(SoftSpec.model_fields)  # 후방호환 + 일반화


# ───────────────────────── 순수 랭킹 ─────────────────────────


def test_gym_required_orders_yes_above_unknown_above_no() -> None:
    cands = [
        _cand("NO", gym="no", gym_conf=0.6),
        _cand("UNK", gym="unknown", gym_conf=0.5),
        _cand("YES", gym="yes", gym_conf=0.9),
    ]
    ranked = rank_candidates(cands, SoftSpec(gym="required"))
    assert _ids(ranked) == ["YES", "UNK", "NO"]  # yes > unknown/none > no
    assert len(ranked) == len(cands)  # SET 불변


def test_pet_required_orders_yes_conditional_unknown_no() -> None:
    cands = [
        _cand("NO", pet="no", pet_conf=0.6),
        _cand("UNK", pet="unknown", pet_conf=0.3),
        _cand("COND", pet="conditional", pet_conf=0.55),
        _cand("YES", pet="yes", pet_conf=0.9),
    ]
    ranked = rank_candidates(cands, SoftSpec(pet="required"))
    assert _ids(ranked) == ["YES", "COND", "UNK", "NO"]


def test_required_outweighs_preferred() -> None:
    # A: gym yes(preferred) only. B: pet yes(required) only. required 가중이 더 커 B가 위.
    a = _cand("A", gym="yes", gym_conf=0.9, pet="no", pet_conf=0.9)
    b = _cand("B", gym="no", gym_conf=0.9, pet="yes", pet_conf=0.9)
    ranked = rank_candidates([a, b], SoftSpec(gym="preferred", pet="required"))
    assert _ids(ranked) == ["B", "A"]


def test_higher_confidence_ranks_higher_within_same_state() -> None:
    cands = [_cand("LO", pet="yes", pet_conf=0.3), _cand("HI", pet="yes", pet_conf=0.95)]
    ranked = rank_candidates(cands, SoftSpec(pet="required"))
    assert _ids(ranked) == ["HI", "LO"]  # conf가 긍정 상태 내 순위 변조


def test_demote_not_exclude_keeps_unmatched_required() -> None:
    # required인데 전부 no/none이어도 제외 0 — 전원 잔존(하위 랭킹).
    cands = [_cand("X", pet="no", pet_conf=0.9), _cand("Y"), _cand("Z", pet="no", pet_conf=0.5)]
    ranked = rank_candidates(cands, SoftSpec(pet="required"))
    assert set(_ids(ranked)) == {"X", "Y", "Z"}  # 집합 불변
    assert len(ranked) == 3


def test_soft_none_preserves_input_order() -> None:
    cands = [_cand("C1", pet="no"), _cand("C2", pet="yes"), _cand("C3", pet="unknown")]
    ranked = rank_candidates(cands, SoftSpec())  # all none
    assert _ids(ranked) == ["C1", "C2", "C3"]  # 순서 불변


def test_ties_preserve_neutral_input_order() -> None:
    # 동점(둘 다 none) → 안정 정렬로 입력 순서 유지.
    cands = [_cand("FIRST"), _cand("SECOND"), _cand("THIRD")]
    ranked = rank_candidates(cands, SoftSpec(gym="required"))
    assert _ids(ranked) == ["FIRST", "SECOND", "THIRD"]


# ───────────────────────── 라우트 통합 (SET 불변) ─────────────────────────


@pytest.fixture
def client(search_db: sqlite3.Connection) -> Iterator[TestClient]:
    # C2에 pet yes, C1에 pet no 시드 — required pet이면 C2가 C1 위로(둘 다 잔존).
    now = datetime.now(UTC)
    write_facts(search_db, "C2", "pet_allowed",
                [_pet_fact("yes", confidence=0.9, source_type="official",
                           source_url="https://o/2")], ttl=timedelta(days=90), now=now)
    write_facts(search_db, "C1", "pet_allowed",
                [_pet_fact("no", confidence=0.6, source_type="agent_research",
                           source_url="urn:x:C1")], ttl=timedelta(days=90), now=now)

    def _override() -> Iterator[sqlite3.Connection]:
        yield search_db

    app.dependency_overrides[get_db] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_route_soft_reorders_without_changing_set(client: TestClient) -> None:
    neutral = client.post("/complexes/search", json={}).json()
    ranked = client.post("/complexes/search", json={"soft": {"pet": "required"}}).json()
    # SET(하드와 직교) 불변 — soft는 ORDER만.
    assert {c["complex_id"] for c in neutral} == {c["complex_id"] for c in ranked}
    assert len(neutral) == len(ranked)
    # pet yes(C2)가 pet no(C1)보다 위.
    order = [c["complex_id"] for c in ranked]
    assert order.index("C2") < order.index("C1")


def test_route_soft_none_matches_neutral_order(client: TestClient) -> None:
    neutral = [c["complex_id"] for c in client.post("/complexes/search", json={}).json()]
    none_body = {"soft": {"gym": "none", "pet": "none"}}
    explicit = [c["complex_id"] for c in client.post("/complexes/search", json=none_body).json()]
    assert neutral == explicit  # none → 순서 불변
