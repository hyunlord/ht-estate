"""조건 레지스트리 — soft/hard 단일 소스 카탈로그 (P4-2a soft 일반화).

고정 {gym,pet} → **임의 조건**. 각 조건은 (key·label·value_type·direction) 메타 + 선택적
`soft_scorer`(soft-able면) + hard 가능성 플래그를 갖는다. 랭킹은 활성 조건의 가중합(ranking.py),
hard 필터는 repo._complex_where가 HardFilterSpec 필드로 수행(레지스트리가 hard-able을 명시).

핵심 불변식 **demote-not-exclude**: soft scorer는 후보를 절대 drop하지 않는다 — 데이터없음/unknown은
중립 baseline(NEUTRAL)로 강등, 확인된 mismatch는 더 낮게(그래도 제외 아님). gym/pet scorer는 기존
ranking._signal과 **동일 점수**를 재현(후방호환). 구조화 수치 scorer는 coarse monotonic(포화 cap) —
정밀 calibration·가중 튜닝은 #2b/#3 소관(여기는 백엔드 토대).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from app.search.repo import Candidate

# 정보 부재(unknown/none/미부착) 중립 baseline — gym/pet과 동일(부적합 아님, 강등용).
NEUTRAL = 0.3

# 역세권 ordinal — K-apt kaptdWtimesub 카테고리(가까울수록 좋음). 밖이면 None(미상).
SUBWAY_RANK: dict[str, int] = {
    "5분이내": 0, "5~10분이내": 1, "10~15분이내": 2, "15~20분이내": 3, "20분이상": 4,
}
_SUBWAY_MAX_RANK = 4


class CriterionEval(BaseModel):
    """후보×조건 평가 — API 표면화(설계 §7 조건별 ✓/△/✗) + 랭킹 입력.

    score는 [0,1](랭킹 가중합 단위). status: match(✓)·partial(△)·miss(✗)·unknown(데이터 없음).
    value는 원값(표시용). demote-not-exclude라 score는 낮아질 뿐 후보를 빼지 않는다.
    """

    key: str
    label: str
    value: object | None
    score: float
    confidence: float | None
    status: str


def _status(score: float, *, has_data: bool) -> str:
    if not has_data:
        return "unknown"
    if score >= 0.6:
        return "match"
    if score > NEUTRAL:
        return "partial"
    return "miss"


def _state_signal(state: str, confidence: float | None) -> float:
    """gym/pet 상태→[0,1] — 기존 ranking._signal과 **동일**(후방호환)."""
    c = confidence if confidence is not None else 0.0
    if state == "yes":
        return 0.6 + 0.4 * c
    if state == "conditional":
        return 0.5 + 0.3 * c
    if state == "no":
        return 0.1
    return NEUTRAL  # unknown · none · 미부착


def _numeric_signal(value: float | None, cap: float) -> tuple[float, bool]:
    """수치(클수록 좋음)→[0.1,1.0], 없으면 NEUTRAL. 포화 cap으로 정규화(coarse·#2b 보정)."""
    if value is None:
        return NEUTRAL, False
    return 0.1 + 0.9 * min(1.0, max(0.0, value) / cap), True


# ───────────────────────── soft scorers (Candidate → CriterionEval) ─────────────────────────


def _score_gym(c: Candidate) -> tuple[object, float, float | None, bool]:
    s = c.gym
    state = s.has_gym if s is not None else "none"
    conf = s.confidence if s is not None else None
    return state, _state_signal(state, conf), conf, state not in ("none",)


def _score_pet(c: Candidate) -> tuple[object, float, float | None, bool]:
    s = c.pet
    state = s.pet_allowed if s is not None else "none"
    conf = s.confidence if s is not None else None
    return state, _state_signal(state, conf), conf, state not in ("none",)


def _score_bool(value: bool | None) -> tuple[object, float, float | None, bool]:
    """bool 조건 — True=1.0(conf 1.0)·False=0.1·없음=NEUTRAL(demote-not-exclude)."""
    if value is None:
        return None, NEUTRAL, None, False
    return value, (1.0 if value else 0.1), 1.0, True


def _score_subway(c: Candidate) -> tuple[object, float, float | None, bool]:
    rank = SUBWAY_RANK.get(c.subway_time or "")
    if c.subway_time is None or rank is None:
        return c.subway_time, NEUTRAL, None, False
    return c.subway_time, 0.1 + 0.9 * (1 - rank / _SUBWAY_MAX_RANK), 1.0, True


def _numeric_scorer(
    attr: str, cap: float
) -> Callable[[Candidate], tuple[object, float, float | None, bool]]:
    def scorer(c: Candidate) -> tuple[object, float, float | None, bool]:
        value = getattr(c, attr)
        score, has = _numeric_signal(None if value is None else float(value), cap)
        return value, score, (1.0 if has else None), has

    return scorer


def _score_approval_year(c: Candidate) -> tuple[object, float, float | None, bool]:
    """신축일수록 좋음 — approval_date 'YYYY..'에서 연도. 1980~2025로 정규화."""
    year: int | None = None
    if c.approval_date and len(c.approval_date) >= 4 and c.approval_date[:4].isdigit():
        year = int(c.approval_date[:4])
    if year is None:
        return None, NEUTRAL, None, False
    norm = min(1.0, max(0.0, (year - 1980) / (2025 - 1980)))
    return year, 0.1 + 0.9 * norm, 1.0, True


@dataclass(frozen=True)
class Criterion:
    """레지스트리 한 항목 — soft/hard 단일 소스 메타.

    soft_scorer None이면 soft-able 아님(예: heat_type·builder는 hard 매칭만 의미). hard_able이면
    repo._complex_where가 hard_field(HardFilterSpec)로 in/out 필터(레지스트리는 카탈로그·검증용).
    """

    key: str
    label: str
    source: str  # 'enrichment:<attr>' | 'complex:<column>'
    value_type: str  # 'state' | 'bool' | 'numeric' | 'categorical'
    direction: str  # 'higher_better' | 'lower_better' | 'match'
    soft_scorer: Callable[[Candidate], tuple[object, float, float | None, bool]] | None
    hard_able: bool
    hard_fields: tuple[str, ...]  # HardFilterSpec 필드명(hard_able일 때)

    @property
    def soft_able(self) -> bool:
        return self.soft_scorer is not None

    def evaluate(self, cand: Candidate) -> CriterionEval:
        """후보 평가 — soft-able일 때만. (soft_able 아니면 호출부가 거른다.)"""
        assert self.soft_scorer is not None, f"{self.key} is not soft-able"
        value, score, conf, has = self.soft_scorer(cand)
        return CriterionEval(
            key=self.key, label=self.label, value=value, score=score,
            confidence=conf, status=_status(score, has_data=has),
        )


# 단일 소스 카탈로그. 새 조건은 여기 한 줄(+ hard면 repo/spec 와이어).
REGISTRY: dict[str, Criterion] = {
    c.key: c
    for c in (
        # enrichment (soft-only — hard filter 밖, R1)
        Criterion("gym", "헬스장", "enrichment:gym", "state", "higher_better",
                  _score_gym, False, ()),
        Criterion("pet", "반려동물", "enrichment:pet_allowed", "state", "higher_better",
                  _score_pet, False, ()),
        # 구조화 (soft + hard)
        Criterion("subway_time", "역세권(지하철 도보)", "complex:subway_time", "categorical",
                  "lower_better", _score_subway, True, ("subway_walkable",)),
        Criterion("has_daycare", "어린이집", "complex:has_daycare", "bool", "higher_better",
                  lambda c: _score_bool(c.has_daycare), True, ("has_daycare",)),
        Criterion("elevator_count", "승강기 수", "complex:elevator_count", "numeric",
                  "higher_better", _numeric_scorer("elevator_count", 20.0), True,
                  ("elevator_count_min",)),
        Criterion("cctv_count", "CCTV 수", "complex:cctv_count", "numeric", "higher_better",
                  _numeric_scorer("cctv_count", 200.0), True, ("cctv_count_min",)),
        Criterion("parking_ratio", "세대당 주차", "complex:parking_ratio", "numeric",
                  "higher_better", _numeric_scorer("parking_ratio", 1.5), True,
                  ("parking_ratio_gte",)),
        Criterion("household_count", "세대수", "complex:household_count", "numeric",
                  "higher_better", _numeric_scorer("household_count", 2000.0), True,
                  ("household_count_min", "household_count_max")),
        Criterion("approval_year", "신축 정도", "complex:approval_date", "numeric",
                  "higher_better", _score_approval_year, True,
                  ("approval_year_min", "approval_year_max")),
        Criterion("top_floor", "최고층", "complex:top_floor", "numeric", "higher_better",
                  _numeric_scorer("top_floor", 40.0), True, ("top_floor_min",)),
        # 구조화 (hard-only — categorical 매칭, 내재 순서 없어 soft 랭킹 부적합)
        Criterion("heat_type", "난방방식", "complex:heat_type", "categorical", "match",
                  None, True, ("heat_type",)),
        Criterion("builder", "건설사", "complex:builder", "categorical", "match",
                  None, True, ("builder",)),
    )
}
