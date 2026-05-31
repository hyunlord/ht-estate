"""soft 랭킹 — gym/pet enrichment 점수로 후보 **순서**만 바꾼다(설계 §7).

핵심 불변식 **demote-not-exclude**: soft는 후보 SET을 절대 안 바꾼다(하드만 in/out). required인데
부재/저신뢰/unknown/no여도 제외 0 — 점수만 낮춰 하위로(정보 부족 ≠ 부적합, 확인된 mismatch도
하드 제외 아님). soft none이면 순서 불변(중립 정렬 유지).

점수 = Σ(속성별) pref_weight × signal. signal은 상태값(양의 선호 기준)에 confidence를 가미:
- yes        ∈ [0.6, 1.0]  (0.6 + 0.4·conf)   — confidence가 긍정 주장의 확신도를 변조
- conditional∈ [0.5, 0.8]  (0.5 + 0.3·conf)   — 허용하되 제한(pet)
- unknown/none = 0.3                            — 정보 부재(중립 baseline, 부적합 아님)
- no           = 0.1                            — 확인된 부정(최하위, 그래도 제외 아님)
required(2.0) > preferred(1.0) > none(0.0=기여 0). 다속성은 가중합(사전식 아님 — 한 속성이
전부를 지배하지 않게). 동점은 안정 정렬로 중립 순서(대표거래 최근 desc) 보존.
"""

from __future__ import annotations

from app.search.repo import Candidate
from app.search.spec import SoftSpec

_PREF_WEIGHT = {"required": 2.0, "preferred": 1.0, "none": 0.0}


def _signal(state: str, confidence: float | None) -> float:
    """상태 + confidence → [0,1] 신호. 양의 선호 기준(yes 높음, no 낮음, 부재는 중립)."""
    c = confidence if confidence is not None else 0.0
    if state == "yes":
        return 0.6 + 0.4 * c
    if state == "conditional":
        return 0.5 + 0.3 * c
    if state == "no":
        return 0.1
    return 0.3  # unknown · none · 미부착(방어) — 정보 부재 중립 baseline


def _score(cand: Candidate, soft: SoftSpec) -> float:
    """후보 soft 점수 — 켜진(none 아님) 속성만 pref_weight × signal 가중합."""
    score = 0.0
    gym_w = _PREF_WEIGHT[soft.gym]
    if gym_w:
        state = cand.gym.has_gym if cand.gym is not None else "none"
        conf = cand.gym.confidence if cand.gym is not None else None
        score += gym_w * _signal(state, conf)
    pet_w = _PREF_WEIGHT[soft.pet]
    if pet_w:
        state = cand.pet.pet_allowed if cand.pet is not None else "none"
        conf = cand.pet.confidence if cand.pet is not None else None
        score += pet_w * _signal(state, conf)
    return score


def rank_candidates(candidates: list[Candidate], soft: SoftSpec) -> list[Candidate]:
    """soft 점수 desc로 후보 재정렬. SET 불변(reorder만 — demote-not-exclude).

    soft가 전부 none이면 입력 순서(중립 정렬) 그대로 반환. 점수 동점은 안정 정렬로
    중립 순서 보존(입력이 이미 대표거래 최근 desc).
    """
    if soft.gym == "none" and soft.pet == "none":
        return list(candidates)  # 순서 불변
    # 안정 정렬: 동점은 입력(중립) 순서 유지. 음수 점수로 desc.
    return sorted(candidates, key=lambda c: -_score(c, soft))
