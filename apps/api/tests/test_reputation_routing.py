"""검색 NL 평판 의도 → reputation_query 추출 (reputation-routing). 키리스(mock LLM).

규칙 #4 재작성: 주관적 평판 구절은 드롭(unsupported) 대신 reputation_query로 추출. ★구조-vs-평판
구분(필터 된 구절은 reputation_query로 안 감·이중처리 0)·순수구조→None(false 라우팅 0). read-only.
"""

from __future__ import annotations

import json

from app.search.nl_parse import parse_query


def _runner(payload: dict):  # type: ignore[no-untyped-def]
    def run(prompt: str, max_turns: int) -> str:
        return json.dumps(payload)
    return run


def test_reputation_query_extracted_with_structure() -> None:
    # "관리 잘 되는 조용한 신축 84" → reputation_query + hard net_area + soft approval_year.
    payload = {
        "hard": {"net_area_min": 84},
        "soft": {"criteria": [{"key": "approval_year", "weight": 1.0}]},
        "detected": [], "unsupported": [],
        "reputation_query": "관리 잘 되는 조용한",
    }
    p = parse_query("관리 잘 되는 조용한 신축 84", runner=_runner(payload))
    assert p.reputation_query == "관리 잘 되는 조용한"  # 주관 의도 드롭 안 됨
    assert p.spec.net_area_min == 84  # 구조 부분 정상 파싱
    assert "approval_year" in {k for k, _ in p.spec.soft.active_criteria()}


def test_reputation_query_subjective_phrases() -> None:
    for q in ("주차 평 좋은", "동네 분위기 좋은", "평판 좋은"):
        payload = {"hard": {}, "soft": {}, "detected": [], "unsupported": [],
                   "reputation_query": q}
        assert parse_query(q, runner=_runner(payload)).reputation_query == q


def test_structure_not_double_processed() -> None:
    # ★ 필터/조건이 된 구절은 reputation_query로 안 감(이중처리 0). 모델이 주차→parking 필터로
    # 보내고 reputation_query=null로 응답 → 구조만, 평판 라우팅 없음.
    payload = {
        "hard": {"parking_ratio_gte": 1.0}, "soft": {},
        "detected": [{"phrase": "주차 넉넉한", "criterion_key": "parking_ratio", "mode": "hard"}],
        "unsupported": [], "reputation_query": None,
    }
    p = parse_query("주차 넉넉한 단지", runner=_runner(payload))
    assert p.spec.parking_ratio_gte == 1.0
    assert p.reputation_query is None  # 필터된 구절은 평판으로 안 감


def test_pure_structure_no_reputation() -> None:
    # 순수 구조 쿼리 → reputation_query None(false 라우팅 0).
    payload = {"hard": {"net_area_min": 84},
               "soft": {"criteria": [{"key": "subway_time", "weight": 1.0}]},
               "detected": [], "unsupported": []}  # reputation_query 키 자체 없음
    p = parse_query("지하철 가까운 신축 84", runner=_runner(payload))
    assert p.reputation_query is None


def test_empty_reputation_query_is_none() -> None:
    # 빈 문자열/공백 → None(false 라우팅 0).
    for val in ("", "   ", None):
        payload = {"hard": {}, "soft": {}, "detected": [], "unsupported": [],
                   "reputation_query": val}
        assert parse_query("x", runner=_runner(payload)).reputation_query is None


def test_non_str_reputation_query_is_none() -> None:
    payload = {"hard": {}, "soft": {}, "detected": [], "unsupported": [], "reputation_query": 123}
    assert parse_query("x", runner=_runner(payload)).reputation_query is None


def test_reputation_with_assigned_school_coexist() -> None:
    # 배정(구조) + 평판(주관) 공존 — 둘 다 추출, 이중처리 없음.
    payload = {"hard": {"assigned_school": "서울잠원초"}, "soft": {},
               "detected": [], "unsupported": [], "reputation_query": "조용한"}
    p = parse_query("서울잠원초 배정 조용한 단지", runner=_runner(payload))
    assert p.spec.assigned_school == "서울잠원초" and p.reputation_query == "조용한"


def test_existing_fields_no_regression() -> None:
    # 기존 hard/soft/unsupported 무회귀(reputation 필드 추가가 기존 동작 안 깸).
    payload = {"hard": {"net_area_min": 84}, "soft": {"gym": "preferred"},
               "detected": [], "unsupported": ["바다 전망"]}
    p = parse_query("바다 전망 헬스장 84", runner=_runner(payload))
    assert p.spec.net_area_min == 84 and p.spec.soft.gym == "preferred"
    assert p.unsupported == ["바다 전망"] and p.reputation_query is None
