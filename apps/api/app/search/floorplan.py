"""floorplan 뷰 레이어 — 후보에 Tier-2 평면도 객관 feature를 부착(설계 §7 카드, P3-2).

gym/pet/review 패턴 재사용(공유 enrichment.read_through_synth + EnrichSource)하되 floorplan 고유:
- 상태/요약이 아니라 **객관 feature**(bay·orientation·structure) — 평면도 VLM 추출(스파이크 검증).
- **표시 전용** — `rank_candidates`에 안 들어간다(랭킹 신호 아님). 카드에 feature+출처만.
- **null-tolerant** — 못 읽은 필드는 null(안전 degrade), 무사실은 전부 null/none → 카드 미표시.
- §11: "좋은 구조" 같은 점수화 금지 — feature는 중립 사실(파서가 도메인 강제, 여긴 표시만).

R1 불변식: floorplan은 hard filter(repo) 밖, 후보 산출 후 부착. enrich(stub) 읽기 전용.
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
from app.search.enrichment import EnrichSource, read_through_synth

ATTRIBUTE = "floorplan"
# enrichment 신선도 — 로더(load_floorplan_seed)와 동일한 분기 기본값.
FLOORPLAN_TTL = timedelta(days=90)


class FloorplanSummary(BaseModel):
    """후보 카드의 평면도 행. primary(최고 confidence) feature + 출처 딥링크.

    bay·orientation·structure 각각 null 가능(못 읽음). 셋 다 null이면 none. **랭킹 신호 아님.**
    """

    bay: int | None
    orientation: str | None
    structure: str | None
    evidence: str | None
    confidence: float | None
    sources: list[EnrichSource]


class _FloorplanTarget(Protocol):
    """attach_floorplan 대상 — complex_id 읽기 + floorplan 쓰기 가능한 후보(repo.Candidate)."""

    complex_id: str
    floorplan: FloorplanSummary | None


def _parse(fact: EnrichmentFact) -> tuple[int | None, str | None, str | None, str | None]:
    """fact.value(JSON{bay,orientation,structure,evidence}) → 필드. graceful(실패는 전부 null)."""
    try:
        data = json.loads(fact.value)
    except (json.JSONDecodeError, TypeError):
        return None, None, None, None
    bay = data.get("bay")
    return (
        bay if isinstance(bay, int) and not isinstance(bay, bool) else None,
        data.get("orientation") if isinstance(data.get("orientation"), str) else None,
        data.get("structure") if isinstance(data.get("structure"), str) else None,
        str(data.get("evidence")) if data.get("evidence") else None,
    )


def synthesize_floorplan(facts: list[EnrichmentFact]) -> FloorplanSummary:
    """출처별 사실 → FloorplanSummary. 무사실 → 전부 null. 다출처면 최고 conf가 primary."""
    if not facts:
        return FloorplanSummary(
            bay=None, orientation=None, structure=None, evidence=None, confidence=None, sources=[]
        )
    primary = max(facts, key=lambda f: f.confidence)
    bay, orientation, structure, evidence = _parse(primary)
    return FloorplanSummary(
        bay=bay,
        orientation=orientation,
        structure=structure,
        evidence=evidence,
        confidence=primary.confidence,
        sources=[EnrichSource(source_type=f.source_type, source_url=f.source_url) for f in facts],
    )


def attach_floorplan(
    conn: sqlite3.Connection,
    candidates: Sequence[_FloorplanTarget],
    *,
    now: datetime,
    ttl: timedelta = FLOORPLAN_TTL,
    extractor: Extractor = stub_extractor,
) -> None:
    """후보들에 floorplan 합성을 in-place 부착(공유 read-through 위임). enrich(stub) 읽기 전용.

    **표시 전용** — 부착만 하고 랭킹은 건드리지 않는다(rank_candidates는 gym/pet만 본다).
    """
    summaries = read_through_synth(
        conn, [c.complex_id for c in candidates], ATTRIBUTE, synthesize_floorplan,
        now=now, ttl=ttl, extractor=extractor,
    )
    for cand in candidates:
        cand.floorplan = summaries[cand.complex_id]
