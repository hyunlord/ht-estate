"""pet 뷰 레이어 — 후보에 Tier-2 pet_allowed 사실을 부착(설계 §7 카드).

gym(P1-2b) 패턴 재사용(공유 enrichment.read_through_synth + EnrichSource)하되 pet 고유:
- 상태 도메인 {yes, conditional, no, unknown}(+none) — conditional이 추가(허용하되 제한).
- caveats(견종/무게/마릿수 등 제한 단서) 보존.
- confirm_with_office — §6·§11 "가장 약한 고리": 카드가 '관리사무소 확인' 권고를 표면화.

R1 불변식: pet은 hard filter(repo) 밖, 후보 산출 후 부착. enrich(stub) 읽기 전용
(hit=시드, miss=무결과→DB 불변). live(키/로컬모델)에서 실추출기 주입 시 같은 경로로 전환.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol

from pydantic import BaseModel

from app.enrich.runner import Extractor, stub_extractor
from app.enrich.store import EnrichmentFact
from app.search.enrichment import EnrichSource, read_through_synth_aliased

# 정식 attribute = 'pet'(라이브 추출 write·E1-live). 레거시 시드(load_pet_seed)는 'pet_allowed'.
# **읽기-타임 별칭**으로 통일(신선 'pet' 우선·없으면 'pet_allowed' 폴백). 데이터 마이그레이션 0.
ATTRIBUTE = "pet"
ALIAS_ATTRIBUTES = ("pet_allowed",)
# enrichment 신선도 — 로더(load_pet_seed)와 동일한 분기 기본값.
PET_TTL = timedelta(days=90)

# pet_allowed 상태 도메인. 'none'은 시드 없음(미조사)이라 'unknown'(조사했으나 불명)과 구분.
_STATES = {"yes", "conditional", "no", "unknown"}


class PetSummary(BaseModel):
    """후보 카드의 pet 행. primary(최고 confidence) 합성 + 제한 caveats + 확인 권고 + 출처.

    pet_allowed: 'yes'|'conditional'|'no'|'unknown'|'none'. none = 시드 없음(미조사).
    confirm_with_office: 관리사무소 확인 권고(pet은 사실상 전수 true) — 카드가 표면화.
    """

    pet_allowed: str
    confidence: float | None
    evidence: str | None
    caveats: list[str]
    confirm_with_office: bool
    sources: list[EnrichSource]


class _PetTarget(Protocol):
    """attach_pet 대상 — complex_id 읽기 + pet 쓰기 가능한 후보(repo.Candidate)."""

    complex_id: str
    pet: PetSummary | None


def _parse(fact: EnrichmentFact) -> tuple[str, str, list[str], bool]:
    """fact.value(JSON{pet_allowed, evidence, caveats, confirm_with_office}) → 필드. graceful.

    파싱 실패/도메인 밖은 'unknown'으로 떨어뜨리고, confirm_with_office 누락은 보수적 true.
    """
    try:
        data = json.loads(fact.value)
    except (json.JSONDecodeError, TypeError):
        return "unknown", "", [], True
    pet = data.get("pet_allowed")
    evidence = data.get("evidence") or ""
    raw_caveats = data.get("caveats")
    caveats = [str(c) for c in raw_caveats] if isinstance(raw_caveats, list) else []
    confirm = bool(data.get("confirm_with_office", True))
    return (pet if pet in _STATES else "unknown"), str(evidence), caveats, confirm


def synthesize_pet(facts: list[EnrichmentFact]) -> PetSummary:
    """출처별 사실 → PetSummary. 무사실 → 'none'. 다출처면 최고 confidence가 primary."""
    if not facts:
        return PetSummary(
            pet_allowed="none", confidence=None, evidence=None,
            caveats=[], confirm_with_office=True, sources=[],
        )

    primary = max(facts, key=lambda f: f.confidence)
    pet, evidence, caveats, confirm = _parse(primary)
    return PetSummary(
        pet_allowed=pet,
        confidence=primary.confidence,
        evidence=evidence,
        caveats=caveats,
        confirm_with_office=confirm,
        sources=[EnrichSource(source_type=f.source_type, source_url=f.source_url) for f in facts],
    )


def attach_pet(
    conn: sqlite3.Connection,
    candidates: Sequence[_PetTarget],
    *,
    now: datetime,
    ttl: timedelta = PET_TTL,
    extractor: Extractor = stub_extractor,
) -> None:
    """후보들에 pet 합성을 in-place 부착(공유 read-through·별칭 위임). enrich(stub) 읽기 전용."""
    summaries = read_through_synth_aliased(
        conn, [c.complex_id for c in candidates], ATTRIBUTE, ALIAS_ATTRIBUTES, synthesize_pet,
        now=now, ttl=ttl, extractor=extractor,
    )
    for cand in candidates:
        cand.pet = summaries[cand.complex_id]
