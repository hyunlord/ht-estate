"""review 뷰 (P3-1) — synthesize(primary/다출처/none) · attach · **랭킹 불변** (키리스)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.enrich.store import EnrichmentFact, write_facts
from app.search.repo import Candidate
from app.search.review import attach_review, synthesize_review
from app.store.db import get_connection, init_db

NOW = datetime(2026, 6, 1, tzinfo=UTC)
TTL = timedelta(days=90)


def _fact(
    summary: str, *, conf: float, url: str, points: list[str] | None = None
) -> EnrichmentFact:
    return EnrichmentFact(
        value=json.dumps({"summary": summary, "points": points or []}, ensure_ascii=False),
        confidence=conf, source_type="youtube", source_url=url,
    )


def test_synthesize_none_when_no_facts() -> None:
    s = synthesize_review([])
    assert s.summary is None and s.points == [] and s.sources == []


def test_synthesize_primary_is_highest_confidence() -> None:
    facts = [
        _fact("낮은 신뢰 요약", conf=0.2, url="https://a/1"),
        _fact("높은 신뢰 요약", conf=0.5, url="https://b/2", points=["조용"]),
    ]
    s = synthesize_review(facts)
    assert s.summary == "높은 신뢰 요약" and s.points == ["조용"]
    assert s.confidence == 0.5
    assert len(s.sources) == 2  # 다출처 전부 노출


def test_attach_review_reads_through_seed() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO complex (complex_id, name) VALUES ('A', '단지')")
    write_facts(conn, "A", "review_summary",
                [_fact("살기 좋다는 평", conf=0.4, url="https://youtube.com/a")],
                ttl=TTL, now=NOW)
    conn.commit()
    cand = Candidate(
        complex_id="A", name="단지", approval_date=None, parking_ratio=None,
        parking_underground=None, household_count=None, lat=None, lng=None,
        source_url=None, transaction_count=0, price_min=None, price_max=None,
        representative_trade=None,
    )
    attach_review(conn, [cand], now=NOW)
    assert cand.review is not None
    assert cand.review.summary == "살기 좋다는 평"


def test_attach_review_none_for_unseeded() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO complex (complex_id, name) VALUES ('A', '단지')")
    conn.commit()
    cand = Candidate(
        complex_id="A", name="단지", approval_date=None, parking_ratio=None,
        parking_underground=None, household_count=None, lat=None, lng=None,
        source_url=None, transaction_count=0, price_min=None, price_max=None,
        representative_trade=None,
    )
    attach_review(conn, [cand], now=NOW)
    assert cand.review is not None and cand.review.summary is None  # 미조사
