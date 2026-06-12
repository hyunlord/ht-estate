"""nl-fast-parse: 룰 선파서 — 기대 spec·커버리지 라우팅·감지 역산·파싱 캐시. 키리스(LLM 0).

핵심 불변: 룰은 **REGISTRY-grounded 정확** spec을 만들고(기대값 테스트), 전체 고신뢰 소비면 룰·
애매/주관이 남으면 None(LLM 폴백·미파싱 0). _build_parsed 재사용 → LLM 경로와 동일 grounding/감지.
"""

from __future__ import annotations

from app.search.nl_parse import (
    _default_runner,
    _norm_query,
    clear_parse_cache,
    parse_query,
)
from app.search.rule_parse import try_rule_parse


def _spec(q: str):  # type: ignore[no-untyped-def]
    parsed = try_rule_parse(q)
    assert parsed is not None, f"rule 미커버(폴백): {q}"
    return parsed.spec


# ── 기대 spec(정확·grounded) ──
def test_gym_pet_soft_pref() -> None:
    s = _spec("헬스장 있고 강아지 되면 좋고")
    assert s.soft.gym == "preferred" and s.soft.pet == "preferred"


def test_net_area_explicit() -> None:
    assert _spec("전용 84 신축").net_area_min == 84.0
    assert _spec("신축 84").net_area_min == 84.0  # 맨숫자(평형)
    assert _spec("84㎡ 이하").net_area_max == 84.0


def test_pyeong_band() -> None:
    s = _spec("20평대 역세권")
    assert s.net_area_min == round(20 * 3.3058, 1) and s.net_area_max == round(30 * 3.3058, 1)


def test_price_eok() -> None:
    assert _spec("15억 이하 대단지").price_max == 150000
    assert _spec("매매 10억 이상").price_min == 100000


def test_deal_type() -> None:
    assert _spec("전세 역세권").deal_type == "jeonse"
    assert _spec("월세 신축").deal_type == "monthly"


def test_parking_underground_hard_vs_ratio_soft() -> None:
    assert _spec("지하주차 신축").parking_underground is True  # 지하주차 = hard bool
    s = _spec("주차 넉넉한 신축")  # 주차 넉넉 = soft parking_ratio
    assert s.parking_underground is None
    assert {c.key for c in s.soft.criteria} >= {"parking_ratio"}


def test_daycare_hard_vs_soft() -> None:
    assert _spec("어린이집 있는 곳만 역세권").has_daycare is True  # "있는" → hard
    s = _spec("어린이집 역세권")  # 마커 없음 → soft
    assert s.has_daycare is None and {c.key for c in s.soft.criteria} >= {"has_daycare"}


def test_heat_and_property_type() -> None:
    assert _spec("지역난방 신축").heat_type == "지역난방"
    assert _spec("오피스텔 역세권").property_type == "officetel"
    assert _spec("빌라 주차 넉넉한").property_type == "rowhouse"


def test_assigned_school() -> None:
    s = _spec("서울잠원초 배정받는 신축 84")
    assert s.assigned_school == "서울잠원초" and s.net_area_min == 84.0


def test_approval_year_explicit() -> None:
    assert _spec("2015년 이후 역세권").approval_year_min == 2015
    assert _spec("2010년 이전 대단지").approval_year_max == 2010


def test_soft_criteria_full_set() -> None:
    s = _spec("초등 가까운 병원 약국 공원 가까운 마트 편의점 엘베 cctv 신축")
    assert {c.key for c in s.soft.criteria} == {
        "elem_dist", "hospital", "pharmacy", "park", "mart", "conv",
        "elevator_count", "cctv_count", "approval_year",
    }


# ── 커버리지 라우팅 (full→rule · 애매/주관→None 폴백) ──
def test_subjective_falls_back() -> None:
    # 측정 불가 주관/미지 어구 → None(LLM 폴백·reputation_query는 LLM 소관).
    subjective = ("관리 잘 되고 조용한 신축 84", "바다 전망 좋은 곳",
                  "층간소음 적은 아파트", "분위기 좋은 동네")
    for q in subjective:
        assert try_rule_parse(q) is None, q


def test_empty_or_signalless_falls_back() -> None:
    assert try_rule_parse("질의") is None  # 신호 0
    assert try_rule_parse("") is None
    assert try_rule_parse("강남") is None  # 순수 지역(신호 없음) → LLM


def test_region_consumed_no_spec_effect() -> None:
    # 지역명은 소비만(spec 무영향) — 커버리지 통과시키되 필드 안 만든다(지도 뷰포트 담당).
    s = _spec("강남 송파 역세권 신축")
    assert s.has_bbox is False  # 지역명이 bbox/필드를 만들지 않음
    assert {c.key for c in s.soft.criteria} == {"subway_time", "approval_year"}


# ── 감지 역산 (룰 spec서 동일 칩) ──
def test_detected_derived_from_rule_spec() -> None:
    parsed = try_rule_parse("어린이집 있는 역세권 강아지")
    assert parsed is not None
    by = {(d.criterion_key, d.mode) for d in parsed.detected}
    assert ("has_daycare", "hard") in by
    assert ("subway_time", "soft") in by
    assert ("pet", "soft") in by


# ── 파싱 캐시 (parse_query 프로덕션 경로) ──
def test_parse_cache_hit_returns_same() -> None:
    clear_parse_cache()
    a = parse_query("헬스장 역세권 신축", runner=_default_runner)  # 룰 처리(claude -p 미호출)
    b = parse_query("헬스장 역세권 신축", runner=_default_runner)  # 캐시 히트
    assert a is b  # 동일 객체(캐시) — claude -p 스폰 0
    assert _norm_query("  헬스장   역세권 신축 ") == "헬스장 역세권 신축"  # 정규화 키


def test_mock_runner_bypasses_rules() -> None:
    # 테스트 mock 러너 주입 시 룰/캐시 우회 → LLM 경로(기존 거동 보존).
    called = {"n": 0}

    def runner(_prompt: str, _turns: int) -> str:
        called["n"] += 1
        return '{"hard": {"has_daycare": true}, "soft": {"gym":"none","pet":"none","criteria":[]}}'

    parsed = parse_query("헬스장 역세권 신축", runner=runner)  # 룰로 커버되는 쿼리지만
    assert called["n"] == 1  # mock이 호출됨(룰 우회)
    assert parsed.spec.has_daycare is True  # mock 출력 사용
