"""P4-2a soft 일반화 — 레지스트리 완전성·demote-not-exclude(어드버서리얼)·gym/pet 후방호환·
구조화 hard(in/out)+soft(랭킹)·per-조건 평가 표면화·SoftCriterion 검증. 키리스(순수 로직)."""

from __future__ import annotations

import sqlite3

import pytest
from pydantic import ValidationError

from app.search.criteria import NEUTRAL, REGISTRY
from app.search.gym import GymSummary
from app.search.pet import PetSummary
from app.search.ranking import rank_candidates
from app.search.repo import Candidate, search_complexes
from app.search.spec import HardFilterSpec, SoftCriterion, SoftSpec
from app.store.db import get_connection, init_db


def _cand(cid: str, **fields: object) -> Candidate:
    base: dict[str, object] = dict(
        complex_id=cid, name=cid, approval_date=None, parking_ratio=None,
        parking_underground=None, household_count=None, lat=None, lng=None,
        source_url=None, transaction_count=0, price_min=None, price_max=None,
        representative_trade=None,
    )
    base.update(fields)
    return Candidate(**base)  # type: ignore[arg-type]


def _gym(state: str, conf: float = 0.8) -> GymSummary:
    return GymSummary(has_gym=state, confidence=conf, evidence=None, sources=[])


def _ids(cands: list[Candidate]) -> list[str]:
    return [c.complex_id for c in cands]


# ───────────────────────── 레지스트리 완전성 ─────────────────────────


def test_registry_has_expected_criteria_and_flags() -> None:
    # gym/pet(enrichment soft-only) + 구조화(soft+hard) + categorical(hard-only)
    assert {"gym", "pet", "subway_time", "has_daycare", "elevator_count", "cctv_count",
            "parking_ratio", "household_count", "approval_year", "top_floor",
            "heat_type", "builder"} <= set(REGISTRY)
    # gym/pet: soft-only(enrichment) — hard 아님
    for k in ("gym", "pet"):
        assert REGISTRY[k].soft_able and not REGISTRY[k].hard_able
    # 구조화: soft + hard 둘 다
    for k in ("subway_time", "has_daycare", "elevator_count", "cctv_count", "top_floor"):
        assert REGISTRY[k].soft_able and REGISTRY[k].hard_able
    # categorical 매칭: hard-only(내재 순서 없어 soft 부적합)
    for k in ("heat_type", "builder"):
        assert REGISTRY[k].hard_able and not REGISTRY[k].soft_able


# ───────────────────────── demote-not-exclude (어드버서리얼) ─────────────────────────


def test_soft_criterion_demotes_missing_data_never_excludes() -> None:
    # has_daycare soft: True > 데이터없음(None=NEUTRAL) > False. 셋 다 잔존(SET 불변).
    yes = _cand("YES", has_daycare=True)
    missing = _cand("MISS", has_daycare=None)  # 데이터 없음
    no = _cand("NO", has_daycare=False)
    spec = SoftSpec(criteria=[SoftCriterion(key="has_daycare", weight=1.0)])
    ranked = rank_candidates([no, missing, yes], spec)
    assert set(_ids(ranked)) == {"YES", "MISS", "NO"}  # 집합 불변 — 제외 0
    assert _ids(ranked) == ["YES", "MISS", "NO"]  # 데이터없음은 확인된 no보다 위(강등 baseline)


def test_missing_data_scores_neutral_not_zero() -> None:
    miss = _cand("M", elevator_count=None)
    spec = SoftSpec(criteria=[SoftCriterion(key="elevator_count", weight=1.0)])
    rank_candidates([miss], spec)
    assert miss.criteria_eval is not None
    ev = miss.criteria_eval[0]
    assert ev.score == NEUTRAL and ev.status == "unknown"  # 데이터없음 = 중립, 제외 아님


# ───────────────────────── gym/pet 후방호환 ─────────────────────────


def test_legacy_gym_pet_equals_generalized_criteria() -> None:
    # 레거시 SoftSpec(gym=required) ≡ 일반화 criteria=[{gym, weight=2.0}] (required=2.0).
    cands = [_cand("NO", gym=_gym("no", 0.6)), _cand("UNK", gym=_gym("unknown", 0.5)),
             _cand("YES", gym=_gym("yes", 0.9))]
    legacy = rank_candidates(list(cands), SoftSpec(gym="required"))
    general = rank_candidates(
        list(cands), SoftSpec(criteria=[SoftCriterion(key="gym", weight=2.0)]))
    assert _ids(legacy) == _ids(general) == ["YES", "UNK", "NO"]  # 동일 순서


def test_legacy_gym_pet_ordering_preserved() -> None:
    # 기존 랭킹 동작 재현 — required > preferred(가중), conf가 동상태 내 순위 변조.
    a = _cand("A", gym=_gym("yes", 0.9), pet=PetSummary(
        pet_allowed="no", confidence=0.9, evidence=None, caveats=[], confirm_with_office=True,
        sources=[]))
    b = _cand("B", gym=_gym("no", 0.9), pet=PetSummary(
        pet_allowed="yes", confidence=0.9, evidence=None, caveats=[], confirm_with_office=True,
        sources=[]))
    ranked = rank_candidates([a, b], SoftSpec(gym="preferred", pet="required"))
    assert _ids(ranked) == ["B", "A"]  # required(pet) 가중이 preferred(gym) 우위


def test_soft_inactive_preserves_order_no_eval() -> None:
    cands = [_cand("C1"), _cand("C2"), _cand("C3")]
    ranked = rank_candidates(cands, SoftSpec())  # 비활성
    assert _ids(ranked) == ["C1", "C2", "C3"]  # 순서 불변
    assert all(c.criteria_eval is None for c in ranked)  # 평가 미부착


# ───────────────────────── 구조화 조건 hard(in/out) + soft(랭킹) ─────────────────────────


def _db_with(*rows: dict[str, object]) -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    cols = ("complex_id", "name", "subway_time", "has_daycare", "elevator_count", "household_count")
    for r in rows:
        conn.execute(
            f"INSERT INTO complex ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [r.get(c) for c in cols],
        )
    conn.commit()
    return conn


def test_structured_hard_filters_in_out() -> None:
    conn = _db_with(
        {"complex_id": "NEAR", "name": "역세권", "subway_time": "5분이내"},
        {"complex_id": "FAR", "name": "외곽", "subway_time": "20분이상"},
    )
    # 역세권 hard → 가까운 단지만 SET에(in/out).
    got = search_complexes(conn, HardFilterSpec(subway_walkable=True))
    assert _ids(got) == ["NEAR"]
    # has_daycare hard
    conn2 = _db_with(
        {"complex_id": "D", "name": "보육", "has_daycare": 1},
        {"complex_id": "N", "name": "무", "has_daycare": 0},
    )
    assert _ids(search_complexes(conn2, HardFilterSpec(has_daycare=True))) == ["D"]


def test_structured_soft_reorders_set_unchanged() -> None:
    conn = _db_with(
        {"complex_id": "LO", "name": "저", "elevator_count": 2},
        {"complex_id": "HI", "name": "고", "elevator_count": 18},
    )
    base = search_complexes(conn, HardFilterSpec())  # 하드 없음 → 둘 다
    spec = SoftSpec(criteria=[SoftCriterion(key="elevator_count", weight=1.0)])
    ranked = rank_candidates(base, spec)
    assert set(_ids(ranked)) == {"LO", "HI"}  # SET 불변
    assert _ids(ranked) == ["HI", "LO"]  # 승강기 많은 단지가 위


# ───────────────────────── per-조건 평가 표면화 (§7 ✓/△/✗) ─────────────────────────


def test_per_criterion_eval_surfaced() -> None:
    c = _cand("A", gym=_gym("yes", 0.9), has_daycare=True, elevator_count=None)
    spec = SoftSpec(
        gym="required",
        criteria=[SoftCriterion(key="has_daycare"), SoftCriterion(key="elevator_count")],
    )
    rank_candidates([c], spec)
    assert c.criteria_eval is not None
    by = {e.key: e for e in c.criteria_eval}
    assert {"gym", "has_daycare", "elevator_count"} == set(by)
    assert by["gym"].value == "yes" and by["gym"].status == "match" and by["gym"].score > 0.6
    assert by["has_daycare"].value is True and by["has_daycare"].status == "match"
    assert by["elevator_count"].value is None and by["elevator_count"].status == "unknown"


# ───────────────────────── SoftCriterion 검증 ─────────────────────────


def test_softcriterion_rejects_unknown_and_hard_only_keys() -> None:
    with pytest.raises(ValidationError):
        SoftCriterion(key="bogus")  # 미등록
    with pytest.raises(ValidationError):
        SoftCriterion(key="heat_type")  # hard-only(soft 불가)
    with pytest.raises(ValidationError):
        SoftCriterion(key="gym", weight=-1.0)  # 음수 weight
    assert SoftCriterion(key="subway_time").weight == 1.0  # 기본 weight
