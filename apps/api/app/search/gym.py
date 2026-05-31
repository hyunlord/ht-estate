"""gym 뷰 레이어 — 후보에 Tier-2 gym 사실을 부착(설계 §7 카드).

R1 불변식: gym은 hard filter(repo.py)에 절대 들어가지 않는다 — 후보 산출 **후** 부착하는
표시(view) 관심사다. query-time은 `enrich(stub_extractor)`로 **읽기 전용** read-through:
hit=시드 반환, miss=빈 리스트(stub은 무결과 → write-back 없음 → DB 불변).

P1-2-live(키)에서 stub_extractor를 GymExtractor로 교체하면 **같은 호출**이 miss→실추출
lazy 경로로 전환된다(API 모양 불변). 이게 골격을 통해 부착하는 이유.

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

from app.enrich.runner import Extractor, enrich, stub_extractor
from app.enrich.store import EnrichmentFact

ATTRIBUTE = "gym"
# enrichment 신선도 — 로더(load_gym_seed)와 동일한 분기 기본값.
GYM_TTL = timedelta(days=90)

# has_gym 상태 도메인. 'none'은 시드 없음(미조사)이라 'unknown'(조사했으나 불명)과 구분한다.
_STATES = {"yes", "no", "unknown"}


class GymSource(BaseModel):
    """gym 사실 한 출처의 딥링크(출처 이동). http면 클릭, urn sentinel이면 비링크 라벨."""

    source_type: str
    source_url: str


class GymSummary(BaseModel):
    """후보 카드의 gym 행. primary(최고 confidence) 합성 + 전체 출처.

    has_gym: 'yes'|'no'|'unknown'|'none'. none = 시드 없음(미조사) → confidence/evidence None.
    """

    has_gym: str
    confidence: float | None
    evidence: str | None
    sources: list[GymSource]


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
    """출처별 사실 → GymSummary. 무사실 → 'none'. 다출처면 최고 confidence가 primary."""
    if not facts:
        return GymSummary(has_gym="none", confidence=None, evidence=None, sources=[])

    primary = max(facts, key=lambda f: f.confidence)
    has_gym, evidence = _parse(primary)
    return GymSummary(
        has_gym=has_gym,
        confidence=primary.confidence,
        evidence=evidence,
        sources=[GymSource(source_type=f.source_type, source_url=f.source_url) for f in facts],
    )


def attach_gym(
    conn: sqlite3.Connection,
    candidates: Sequence[_GymTarget],
    *,
    now: datetime,
    ttl: timedelta = GYM_TTL,
    extractor: Extractor = stub_extractor,
) -> None:
    """후보들에 gym 합성을 in-place 부착. enrich(stub) read-through(읽기 전용).

    extractor 주입형 — 기본 stub(읽기 전용). P1-2-live에서 GymExtractor 주입 시 같은
    경로가 lazy 실추출로 전환된다(API 불변). candidates는 `.complex_id`·`.gym`을 가진 객체.
    """
    ids = [c.complex_id for c in candidates]
    if not ids:
        return
    facts_by_id = enrich(conn, ids, ATTRIBUTE, extractor, ttl=ttl, now=now)
    for cand in candidates:
        cand.gym = synthesize_gym(facts_by_id.get(cand.complex_id, []))
