"""soft 랭킹 — **활성 조건들의 가중합**으로 후보 순서만 바꾼다(설계 §7, P4-2a 일반화).

핵심 불변식 **demote-not-exclude**: soft는 후보 SET을 절대 안 바꾼다(하드만 in/out). 미충족/부재/
저신뢰/unknown → 강등이지 제외 아님. soft 비활성(none·빈 criteria)이면 순서 불변(중립 정렬 유지).

점수 = Σ_(활성 조건) weight × scorer(cand).score. 조건별 scorer는 criteria.REGISTRY가 보유:
- gym/pet: 상태×confidence 신호(기존 _signal과 **동일** — 후방호환).
- 구조화(역세권·어린이집·승강기·CCTV·주차·세대수 등): coarse monotonic + 데이터없음=NEUTRAL.
조건별 평가(value·score·confidence·status)는 후보에 criteria_eval로 부착(§7 ✓/△/✗ + 프론트 튜닝).
다조건은 가중합(사전식 아님). 동점은 안정 정렬로 중립 순서(대표거래 최근 desc) 보존.
"""

from __future__ import annotations

from app.search.criteria import REGISTRY, CriterionEval
from app.search.repo import Candidate
from app.search.spec import SoftSpec


def _evaluate(cand: Candidate, active: list[tuple[str, float]]) -> list[CriterionEval]:
    """활성 조건별 평가 리스트(soft-able만). REGISTRY scorer 위임."""
    evals: list[CriterionEval] = []
    for key, _weight in active:
        crit = REGISTRY[key]
        if crit.soft_scorer is not None:
            evals.append(crit.evaluate(cand))
    return evals


def _score(evals: list[CriterionEval], weights: dict[str, float]) -> float:
    """평가 가중합 — Σ weight[key] × score."""
    return sum(weights.get(e.key, 0.0) * e.score for e in evals)


def rank_candidates(candidates: list[Candidate], soft: SoftSpec) -> list[Candidate]:
    """활성 soft 조건 가중합 desc로 재정렬 + 조건별 평가 부착. SET 불변(demote-not-exclude).

    soft 비활성(active 없음)이면 입력 순서(중립 정렬) 그대로 반환(criteria_eval 미부착).
    점수 동점은 안정 정렬로 중립 순서 보존(입력이 이미 대표거래 최근 desc).
    """
    active = soft.active_criteria()
    if not active:
        return list(candidates)  # 순서 불변

    weights: dict[str, float] = {}
    for key, weight in active:
        weights[key] = weights.get(key, 0.0) + weight  # 중복 key는 가중 합산

    scored: list[tuple[float, Candidate]] = []
    for cand in candidates:
        evals = _evaluate(cand, active)
        cand.criteria_eval = evals  # §7 표면화(in-place)
        scored.append((_score(evals, weights), cand))
    # 안정 정렬: 동점은 입력(중립) 순서 유지. 음수 점수로 desc.
    return [cand for _, cand in sorted(scored, key=lambda sc: -sc[0])]
