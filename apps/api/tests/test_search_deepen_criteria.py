"""search-deepen-1 — 학교거리·POI 카테고리 criteria 등록(NL + 랭킹 동시 개방). 키리스.

신규 criteria scorer(lower/higher_better·미적재→NEUTRAL) · NL 파싱(grounded·mock LLM) · 랭킹
(가까운/편의 좋은 상위·미적재 NEUTRAL=꼴찌 아님) · Candidate 하이드레이션(attach 값 READ) ·
기존 REGISTRY/nl_parse/rank 무회귀. read-only(랭킹 JOIN은 attach_*·canon write 0).
"""

from __future__ import annotations

import json

from app.poi.store import PoiNear
from app.school.store import SchoolNear
from app.search.criteria import NEUTRAL, REGISTRY
from app.search.nl_parse import parse_query, registry_catalog
from app.search.ranking import rank_candidates
from app.search.repo import Candidate
from app.search.spec import SoftCriterion, SoftSpec


def _cand(cid: str, *, school: list[SchoolNear] | None = None,
          poi: list[PoiNear] | None = None) -> Candidate:
    return Candidate(
        complex_id=cid, name=cid, approval_date="2010-01-01", parking_ratio=None,
        parking_underground=None, household_count=None, lat=None, lng=None, source_url=None,
        transaction_count=0, price_min=None, price_max=None, representative_trade=None,
        school=school, poi=poi,
    )


def _school(level: str, dist: int | None) -> SchoolNear:
    return SchoolNear(level=level, label=level, nearest_dist_m=dist, nearest_name="x",
                      nearest_school_id="1", count_500m=1, count_1km=1)


def _poi(category: str, *, near: int | None = None, c1km: int | None = None) -> PoiNear:
    return PoiNear(category=category, label=category, nearest_dist_m=near, nearest_name="x",
                   count_500m=None, count_1km=c1km)


# ── 학교거리 scorer (lower_better · 미적재→NEUTRAL) ──
def test_school_dist_scorer_closer_higher() -> None:
    crit = REGISTRY["elem_dist"]
    near = crit.evaluate(_cand("A", school=[_school("elem", 100)]))
    far = crit.evaluate(_cand("B", school=[_school("elem", 950)]))
    assert near.score > far.score  # 가까울수록 높음
    assert near.status == "match"


def test_school_dist_missing_neutral() -> None:
    # 미적재(school 행 없음) → NEUTRAL·unknown(demote-not-exclude, 꼴찌 아님)
    e = REGISTRY["elem_dist"].evaluate(_cand("A", school=None))
    assert e.score == NEUTRAL and e.status == "unknown"
    # 다른 level만 있고 elem 없음 → 역시 NEUTRAL
    e2 = REGISTRY["elem_dist"].evaluate(_cand("B", school=[_school("mid", 100)]))
    assert e2.score == NEUTRAL


def test_school_dist_computed_zero_schools_low_not_neutral() -> None:
    # 계산됐으나 해당 level 학교 0개(nearest None) → 0.1(far)·NEUTRAL 아님(계산됨)
    e = REGISTRY["elem_dist"].evaluate(_cand("A", school=[_school("elem", None)]))
    assert e.score < NEUTRAL and e.status == "miss"


# ── POI 카테고리 scorer ──
def test_poi_count_scorer_more_higher() -> None:
    many = REGISTRY["conv"].evaluate(_cand("A", poi=[_poi("CS2", c1km=15)]))
    few = REGISTRY["conv"].evaluate(_cand("B", poi=[_poi("CS2", c1km=1)]))
    assert many.score > few.score


def test_poi_dist_scorer_closer_higher() -> None:
    near = REGISTRY["hospital"].evaluate(_cand("A", poi=[_poi("HP8", near=100)]))
    far = REGISTRY["hospital"].evaluate(_cand("B", poi=[_poi("HP8", near=1400)]))
    assert near.score > far.score


def test_poi_missing_neutral() -> None:
    # 미적재(카테고리 행 없음) → NEUTRAL (마트/병원 동형)
    assert REGISTRY["mart"].evaluate(_cand("A", poi=None)).score == NEUTRAL
    assert REGISTRY["hospital"].evaluate(_cand("A", poi=[_poi("MT1", c1km=2)])).score == NEUTRAL


def test_poi_computed_zero_low_not_neutral() -> None:
    # 계산된 0건(count=0) → 0.1·NEUTRAL 아님 / 반경내 0건(nearest None) → 0.1
    assert REGISTRY["conv"].evaluate(_cand("A", poi=[_poi("CS2", c1km=0)])).score < NEUTRAL
    assert REGISTRY["park"].evaluate(_cand("A", poi=[_poi("PARK", near=None)])).score < NEUTRAL


# ── 랭킹: 가까운/편의 좋은 상위 · 미적재 NEUTRAL = 꼴찌 아님 ──
def test_ranking_school_dist_orders_and_missing_not_last() -> None:
    near = _cand("near", school=[_school("elem", 120)])      # 가까움 → 상위
    far = _cand("far", school=[_school("elem", None)])       # 학교 0개(계산됨) → 최하
    missing = _cand("missing", school=None)                  # 미적재 → NEUTRAL(중간)
    soft = SoftSpec(criteria=[SoftCriterion(key="elem_dist", weight=1.0)])
    ranked = rank_candidates([far, missing, near], soft)
    ids = [c.complex_id for c in ranked]
    assert ids[0] == "near"          # 가까운 후보 1위
    assert ids.index("missing") < ids.index("far")  # 미적재(NEUTRAL) > 계산된-0(far) → 꼴찌 아님


def test_ranking_poi_missing_neutral_not_excluded() -> None:
    good = _cand("good", poi=[_poi("CS2", c1km=15)])
    missing = _cand("missing", poi=None)
    soft = SoftSpec(criteria=[SoftCriterion(key="conv", weight=1.0)])
    ranked = rank_candidates([missing, good], soft)
    assert len(ranked) == 2  # SET 불변(demote-not-exclude)
    assert ranked[0].complex_id == "good" and ranked[1].complex_id == "missing"


# ── Candidate 하이드레이션 (attach 값 READ — 스코어러 소비) ──
def test_hydration_scorer_reads_attached_lists() -> None:
    # attach_school/attach_poi가 채운 리스트를 스코어러가 직접 READ(별도 JOIN 없이).
    c = _cand("A", school=[_school("elem", 200), _school("mid", 800)],
              poi=[_poi("MT1", c1km=2), _poi("HP8", near=300)])
    assert REGISTRY["elem_dist"].evaluate(c).value == 200
    assert REGISTRY["mid_dist"].evaluate(c).value == 800
    assert REGISTRY["mart"].evaluate(c).value == 2
    assert REGISTRY["hospital"].evaluate(c).value == 300


# ── NL 파싱 (grounded · mock LLM · 미등록 키 거부 유지) ──
def _runner(payload: dict):  # type: ignore[no-untyped-def]
    def run(prompt: str, max_turns: int) -> str:
        return json.dumps(payload)
    return run


def test_nl_parse_school_and_poi_soft() -> None:
    payload = {
        "hard": {}, "soft": {"gym": "none", "pet": "none", "criteria": [
            {"key": "elem_dist", "weight": 1.0}, {"key": "hospital", "weight": 1.0},
            {"key": "conv", "weight": 1.0}]},
        "detected": [{"phrase": "초등 가까운", "criterion_key": "elem_dist", "mode": "soft"}],
        "unsupported": [],
    }
    parsed = parse_query("초등 가까운 병원 편의점", runner=_runner(payload))
    keys = {k for k, _ in parsed.spec.soft.active_criteria()}
    assert {"elem_dist", "hospital", "conv"} <= keys


def test_nl_parse_school_hard_field() -> None:
    payload = {"hard": {"elem_max_dist_m": 500}, "soft": {}, "detected": [], "unsupported": []}
    parsed = parse_query("초등 500m 이내인 곳만", runner=_runner(payload))
    assert parsed.spec.elem_max_dist_m == 500
    # 감지 역산: elem_max_dist_m → elem_dist key
    assert any(d.criterion_key == "elem_dist" and d.mode == "hard" for d in parsed.detected)


def test_nl_parse_poi_hard_fields() -> None:
    payload = {"hard": {"conv_count_1km_min": 5, "hospital_max_dist_m": 800},
               "soft": {}, "detected": [], "unsupported": []}
    parsed = parse_query("편의점 5개 병원 800m", runner=_runner(payload))
    assert parsed.spec.conv_count_1km_min == 5 and parsed.spec.hospital_max_dist_m == 800


def test_nl_parse_rejects_unknown_key_still() -> None:
    # 무회귀: 미등록 soft key는 여전히 drop(grounding 유지)
    payload = {"hard": {}, "soft": {"criteria": [
        {"key": "elem_dist", "weight": 1.0}, {"key": "bogus_key", "weight": 1.0}]},
        "detected": [], "unsupported": []}
    parsed = parse_query("x", runner=_runner(payload))
    keys = {k for k, _ in parsed.spec.soft.active_criteria()}
    assert "elem_dist" in keys and "bogus_key" not in keys


def test_registry_catalog_lists_new_criteria() -> None:
    cat = registry_catalog()
    for k in ("elem_dist", "mid_dist", "high_dist", "mart", "conv", "hospital", "pharmacy", "park"):
        assert f"`{k}`" in cat


# ── 무회귀: 기존 criteria/랭킹 동작 ──
def test_regression_existing_criteria_intact() -> None:
    # 기존 gym/subway/parking 등 그대로
    for k in ("gym", "pet", "subway_time", "parking_ratio", "approval_year", "heat_type"):
        assert k in REGISTRY
    # hard-only(heat_type)는 soft 거부 유지
    try:
        SoftCriterion(key="heat_type")
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_regression_neutral_sort_when_no_soft() -> None:
    a, b = _cand("a"), _cand("b")
    out = rank_candidates([a, b], SoftSpec())  # soft 없음 → 순서 불변
    assert [c.complex_id for c in out] == ["a", "b"]
