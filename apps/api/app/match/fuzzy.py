"""유사도 매칭 — 번호가드 + 포함부스트 + 임계/모호갭. 억지매칭 금지(불확실→None).

라이브 보정(강남 70단지): 이 규칙으로 matched 46%·ambiguous 1·나머지 NULL(고정밀).
recall은 후속(지번 매칭·동 cross-spelling)으로 개선 여지.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from .normalize import name_numbers, normalize_name

# 라이브 보정 임계값. threshold 미만 또는 2등과 gap 미만이면 무매치(NULL).
DEFAULT_THRESHOLD = 0.85
DEFAULT_AMBIGUITY_GAP = 0.05
_CONTAINMENT_SCORE = 0.9


def similarity(query_raw: str, candidate_raw: str) -> float:
    """질의명(query=거래)과 후보명(candidate=단지) 유사도 0..1. 비대칭 — 인자 순서 중요.

    - 정규화 후 동일 → 1.0
    - 번호셋이 양쪽 비어있지 않은데 겹치지 않음(현대5차 vs 6차) → 0.0 (거절)
    - **단방향 포함**: query ⊂ candidate (K-apt가 동/지역 prefix를 덧댄 패턴,
      "미성2차"⊂"압구정미성2차") → 0.9 부스트. 반대 방향(candidate ⊂ query)은
      부스트 안 함 — "청담대림"이 "청담대림이편한세상"의 접두부라고 같은 단지는 아니다
      (라이브 오매칭 사례). 부스트 없으면 SequenceMatcher 비율로 떨어져 임계 미달→NULL.
    - 그 외 → SequenceMatcher 비율
    """
    query, candidate = normalize_name(query_raw), normalize_name(candidate_raw)
    if not query or not candidate:
        return 0.0
    if query == candidate:
        return 1.0
    nq, nc = name_numbers(query), name_numbers(candidate)
    if nq and nc and nq.isdisjoint(nc):
        return 0.0
    base = SequenceMatcher(None, query, candidate).ratio()
    if len(query) >= 2 and query in candidate:
        base = max(base, _CONTAINMENT_SCORE)
    return base


def best_match(
    name: str,
    candidates: list[tuple[str, str]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    ambiguity_gap: float = DEFAULT_AMBIGUITY_GAP,
) -> tuple[str, float] | None:
    """candidates=[(id, name)] 중 최고 유사도 매칭. 불확실하면 None(억지매칭 금지).

    매칭 조건: 최고점 ≥ threshold **그리고** 2등과의 차이 ≥ ambiguity_gap.
    동점/모호(여러 후보가 비슷)면 None — 잘못된 단정보다 NULL이 낫다.
    """
    if not candidates:
        return None
    scored = sorted(
        ((similarity(name, cand_name), cand_id) for cand_id, cand_name in candidates),
        key=lambda x: x[0],
        reverse=True,
    )
    top_score, top_id = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    if top_score >= threshold and (top_score - second_score) >= ambiguity_gap:
        return top_id, top_score
    return None
