"""유사도 매칭 — 동일/포함/번호가드/모호(NULL) + 토큰-인지 회수(monotonic-up·#6-①)."""

from __future__ import annotations

from difflib import SequenceMatcher

from app.match.fuzzy import _token_ratio, best_match, similarity
from app.match.normalize import normalize_name


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


# ── 토큰-인지 회수 (join-recovery #6-①) — 재배열/긴명 회수 + monotonic-up + no-over-merge ──

def test_token_recovery_reordered_blocks() -> None:
    # 블록 재배열(동일 문자구성) — 순차비율은 임계 미달이나 bag Dice가 회수.
    assert similarity("마을현대2단지", "현대2단지마을") >= 0.85
    assert similarity("한울마을1단지", "1단지한울마을") >= 0.85


def test_token_ratio_is_monotonic_up() -> None:
    # similarity는 순차비율보다 절대 낮지 않다(max로만 합산 → 기존 매칭 무손상).
    for q, c in [
        ("역삼래미안", "역삼래미안펜타빌"),
        ("마을현대2단지", "현대2단지마을"),
        ("개포자이", "개포자이프레지던스"),
        ("은마", "타워팰리스"),
    ]:
        qn, cn = normalize_name(q), normalize_name(c)
        seq = SequenceMatcher(None, qn, cn).ratio()
        assert similarity(q, c) >= seq - 1e-9


def test_token_recovery_does_not_break_number_guard() -> None:
    # ★no-over-merge: 차/단지 다르면 토큰 보강이 있어도 0.0 유지(번호가드가 먼저 거절).
    assert similarity("현대1차", "현대2차") == 0.0
    assert similarity("주공1단지", "주공2단지") == 0.0
    assert similarity("래미안1단지마을", "마을래미안2단지") == 0.0  # 재배열+번호충돌 → 거절


def test_token_recovery_preserves_directional_anticase() -> None:
    # candidate ⊂ query(MOLIT명이 더 김) — bag Dice도 길이차로 깎여 임계 미달 유지.
    assert similarity("청담대림이편한세상", "청담대림") < 0.85


def test_token_recovery_no_false_merge_different_names() -> None:
    # 같은 동의 다른 단지(번호 없음·문자 일부 겹침) — 임계 미달로 회수 안 함.
    assert similarity("성원", "현대아이파크") < 0.85
    assert similarity("롯데캐슬", "푸르지오") < 0.85


def test_token_ratio_unit() -> None:
    assert _token_ratio("abc", "abc") == 1.0
    assert _token_ratio("abc", "cba") == 1.0  # 순서 무관
    assert _token_ratio("", "abc") == 0.0
    assert _token_ratio("abcd", "ab") == 2 * 2 / (4 + 2)  # 길이차 페널티
