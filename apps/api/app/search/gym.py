"""gym 뷰 레이어 — 후보에 Tier-2 gym 사실을 부착(설계 §7 카드).

R1 불변식: gym은 hard filter(repo.py)에 절대 들어가지 않는다 — 후보 산출 **후** 부착하는
표시(view) 관심사다. 공통(출처 pair·enrich+합성 read-through)은 enrichment.py 공유,
gym 고유(GymSummary·합성 규칙)만 여기 둔다.

합성: 출처별 다중 사실(value=JSON{has_gym, evidence}) → 최고 confidence를 primary로
(has_gym·confidence·evidence), sources에 전부 노출(다출처 graceful). 시드 없으면 'none'.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol

from pydantic import BaseModel

from app.enrich.extractors.gym_verify import WEB_VERIFIED
from app.enrich.runner import Extractor, stub_extractor
from app.enrich.store import EnrichmentFact
from app.search.enrichment import EnrichSource, read_through_synth
from app.search.gym_kakao import SOURCE_TYPE as KAKAO_LOCAL

ATTRIBUTE = "gym"
# enrichment 신선도 — 로더(load_gym_seed)와 동일한 분기 기본값.
GYM_TTL = timedelta(days=90)

# has_gym 상태 도메인. 'none'은 시드 없음(미조사)이라 'unknown'(조사했으나 불명)과 구분한다.
_STATES = {"yes", "no", "unknown"}


class GymSummary(BaseModel):
    """후보 카드의 gym 행. primary(최고 confidence) 합성 + 전체 출처.

    has_gym: 'yes'|'no'|'unknown'|'none'. none = 시드 없음(미조사) → confidence/evidence None.
    """

    has_gym: str
    confidence: float | None
    evidence: str | None
    sources: list[EnrichSource]


class _GymTarget(Protocol):
    """attach_gym 대상 — complex_id 읽기 + gym 쓰기 가능한 후보(repo.Candidate)."""

    complex_id: str
    gym: GymSummary | None


def _parse(fact: EnrichmentFact) -> tuple[str, str]:
    """fact.value(JSON{has_gym, evidence}) → (has_gym, evidence). 손상/누락은 graceful.

    has_gym가 도메인 밖이거나 파싱 실패면 'unknown'(보수적)으로 떨어뜨린다.
    """
    try:
        data = json.loads(fact.value)
    except (json.JSONDecodeError, TypeError):
        return "unknown", ""
    has_gym = data.get("has_gym")
    evidence = data.get("evidence") or ""
    return (has_gym if has_gym in _STATES else "unknown"), str(evidence)


def synthesize_gym(facts: list[EnrichmentFact]) -> GymSummary:
    """출처별 사실 → GymSummary. Kakao 위치 + doc 검증(web_verified) **결합**(gym-evidence).

    규칙(precision·missing=keep):
    - **yes-wins / no-false-flip**: 어느 소스든 has_gym=yes면 yes(doc no/unknown이 Kakao yes를
      절대 ✗로 안 뒤집음). yes 신호 0일 때만 no/unknown으로 떨어진다(없는 gym 단정 보수).
    - **결합 증거**: Kakao(위치) + doc(검증) 둘 다 yes면 둘 다 표시·두 독립신호 일치 시 conf
      소폭 부스트. 한쪽만이면 그 신호.
    - 무사실 → 'none'(미조사). sources엔 전 출처(Kakao place·검증 doc 딥링크) 보존.
    """
    if not facts:
        return GymSummary(has_gym="none", confidence=None, evidence=None, sources=[])

    sources = [EnrichSource(source_type=f.source_type, source_url=f.source_url) for f in facts]
    parsed = [(f, *_parse(f)) for f in facts]  # (fact, has_gym, evidence)
    yes = [(f, ev) for (f, hg, ev) in parsed if hg == "yes"]
    if yes:
        kakao_ev = next((ev for f, ev in yes if f.source_type == KAKAO_LOCAL and ev), None)
        doc_ev = next((ev for f, ev in yes if f.source_type == WEB_VERIFIED and ev), None)
        parts = [p for p in (kakao_ev, doc_ev) if p]
        evidence = " · ".join(parts) if parts else (next((ev for _, ev in yes if ev), "") or None)
        top = max(f.confidence for f, _ in yes)
        both = kakao_ev is not None and doc_ev is not None  # 두 독립신호 일치 → conf 부스트
        confidence = min(0.97, top + 0.05) if both else top
        return GymSummary(has_gym="yes", confidence=confidence, evidence=evidence, sources=sources)

    # yes 신호 0 → no/unknown 중 최고 confidence(missing=keep: 없는 gym 단정 보수).
    primary = max(facts, key=lambda f: f.confidence)
    has_gym, evidence = _parse(primary)
    return GymSummary(
        has_gym=has_gym, confidence=primary.confidence, evidence=evidence or None, sources=sources
    )


def attach_gym(
    conn: sqlite3.Connection,
    candidates: Sequence[_GymTarget],
    *,
    now: datetime,
    ttl: timedelta = GYM_TTL,
    extractor: Extractor = stub_extractor,
) -> None:
    """후보들에 gym 합성을 in-place 부착(공유 read-through 위임). enrich(stub) 읽기 전용."""
    summaries = read_through_synth(
        conn, [c.complex_id for c in candidates], ATTRIBUTE, synthesize_gym,
        now=now, ttl=ttl, extractor=extractor,
    )
    for cand in candidates:
        cand.gym = summaries[cand.complex_id]
