"""유사도 매칭 — 동일/포함/번호가드/모호(NULL)."""

from __future__ import annotations

from app.match.fuzzy import best_match, similarity


def test_similarity_exact_after_normalize() -> None:
    assert similarity("역삼자이아파트", "역삼자이") == 1.0


def test_similarity_containment_boost_for_dong_prefix() -> None:
    # K-apt의 동/지역 prefix 패턴: "미성2차" ⊂ "압구정미성2차"
    assert similarity("미성2차", "압구정미성2차") >= 0.9


def test_similarity_number_guard_rejects_different_cha() -> None:
    assert similarity("현대5차", "현대6차") == 0.0
    assert similarity("쌍용대치2", "쌍용대치1차") == 0.0
    assert similarity("삼성힐스테이트1단지", "삼성동힐스테이트2단지") == 0.0


def test_similarity_containment_is_directional() -> None:
    # query ⊂ candidate (동-prefix): 부스트 O
    assert similarity("미성2차", "압구정미성2차") >= 0.9
    # candidate ⊂ query (MOLIT명이 더 김): 부스트 X → 임계 미달 (라이브 오매칭 방지)
    # "청담대림"은 "청담대림이편한세상"의 접두부지만 다른 단지일 수 있다
    assert similarity("청담대림이편한세상", "청담대림") < 0.85


def test_similarity_low_for_different_names() -> None:
    assert similarity("은마", "타워팰리스") < 0.5


CANDS = [
    ("A1", "역삼자이아파트"),
    ("A2", "역삼래미안펜타빌"),
    ("A3", "개포자이프레지던스"),
]


def test_best_match_clear_winner() -> None:
    result = best_match("역삼자이", CANDS)
    assert result is not None
    assert result[0] == "A1"
    assert result[1] >= 0.85


def test_best_match_containment_winner() -> None:
    result = best_match("래미안펜타빌", CANDS)  # ⊂ 역삼래미안펜타빌
    assert result is not None
    assert result[0] == "A2"


def test_best_match_none_when_no_candidate_passes() -> None:
    assert best_match("존재하지않는단지", CANDS) is None


def test_best_match_none_when_ambiguous() -> None:
    # 두 후보가 똑같이 포함부스트(0.9) → gap 0 → 모호 → None
    ambiguous = [("B1", "청담2차현대아파트"), ("B2", "청담3차현대아파트")]
    # '현대아파트'는 양쪽에 포함되지만 번호(2/3)가 달라 둘 다 거절될 수도 → None 보장 확인
    assert best_match("현대아파트", ambiguous) is None


def test_best_match_empty_candidates() -> None:
    assert best_match("아무거나", []) is None
