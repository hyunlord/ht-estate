"""review 뷰 레이어 — 후보에 Tier-2 후기 요약을 부착(설계 §7 카드, P3-1).

gym(P1-2b)·pet(C5) 패턴 재사용(공유 enrichment.read_through_synth + EnrichSource)하되 review 고유:
- 상태(state)가 아니라 **요약 텍스트 + 핵심 포인트** — 주관적 평판이라 confidence 보수적.
- **표시 전용** — `rank_candidates`에 들어가지 않는다(랭킹 신호 아님). 카드에 요약+출처만.
- 다출처 보관: 출처별 사실을 모두 sources로 노출, 요약은 최고 confidence가 primary.
- 저작권: 짧은 요약만(파서가 길이 캡). 카드는 요약+출처 딥링크로 출처 이동을 유도.

R1 불변식: review는 hard filter(repo) 밖, 후보 산출 후 부착. enrich(stub) 읽기 전용.
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

ATTRIBUTE = "review_summary"
# enrichment 신선도 — 로더(load_review_seed)와 동일한 분기 기본값.
REVIEW_TTL = timedelta(days=90)


class ReviewSummary(BaseModel):
    """후보 카드의 후기 행. primary(최고 confidence) 요약 + 핵심 포인트 + 전체 출처 딥링크.

    summary: 짧은 자기표현 요약(없으면 None = 미조사). **랭킹 신호 아님**(표시 전용·주관적).
    """

    summary: str | None
    points: list[str]
    confidence: float | None
    sources: list[EnrichSource]


class _ReviewTarget(Protocol):
    """attach_review 대상 — complex_id 읽기 + review 쓰기 가능한 후보(repo.Candidate)."""

    complex_id: str
    review: ReviewSummary | None


def _parse(fact: EnrichmentFact) -> tuple[str, list[str]]:
    """fact.value(JSON{summary, points}) → (summary, points). graceful(실패는 빈 값)."""
    try:
        data = json.loads(fact.value)
    except (json.JSONDecodeError, TypeError):
        return "", []
    summary = str(data.get("summary") or "")
    raw_points = data.get("points")
    points = [str(p) for p in raw_points] if isinstance(raw_points, list) else []
    return summary, points


def synthesize_review(facts: list[EnrichmentFact]) -> ReviewSummary:
    """출처별 사실 → ReviewSummary. 무사실 → summary None(미조사). 다출처면 최고 conf가 primary."""
    if not facts:
        return ReviewSummary(summary=None, points=[], confidence=None, sources=[])
    primary = max(facts, key=lambda f: f.confidence)
    summary, points = _parse(primary)
    return ReviewSummary(
        summary=summary or None,
        points=points,
        confidence=primary.confidence,
        sources=[EnrichSource(source_type=f.source_type, source_url=f.source_url) for f in facts],
    )


def attach_review(
    conn: sqlite3.Connection,
    candidates: Sequence[_ReviewTarget],
    *,
    now: datetime,
    ttl: timedelta = REVIEW_TTL,
    extractor: Extractor = stub_extractor,
) -> None:
    """후보들에 review 합성을 in-place 부착(공유 read-through 위임). enrich(stub) 읽기 전용.

    **표시 전용** — 부착만 하고 랭킹은 건드리지 않는다(rank_candidates는 gym/pet만 본다).
    """
    summaries = read_through_synth(
        conn, [c.complex_id for c in candidates], ATTRIBUTE, synthesize_review,
        now=now, ttl=ttl, extractor=extractor,
    )
    for cand in candidates:
        cand.review = summaries[cand.complex_id]
