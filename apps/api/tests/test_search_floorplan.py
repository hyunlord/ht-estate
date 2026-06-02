"""floorplan 뷰 (P3-2) — synthesize(primary/null/none)·attach·**랭킹 불변** (키리스)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.enrich.store import EnrichmentFact, write_facts
from app.search.floorplan import attach_floorplan, synthesize_floorplan
from app.search.ranking import rank_candidates
from app.search.repo import Candidate
from app.search.spec import SoftSpec
from app.store.db import get_connection, init_db

NOW = datetime(2026, 6, 1, tzinfo=UTC)
TTL = timedelta(days=90)


def _fact(
    *,
    conf: float,
    url: str,
    bay: int | None = 3,
    orientation: str | None = "남향",
    structure: str | None = "판상형",
) -> EnrichmentFact:
    return EnrichmentFact(
        value=json.dumps(
            {"bay": bay, "orientation": orientation, "structure": structure, "evidence": "전면"},
            ensure_ascii=False,
        ),
        confidence=conf, source_type="agent_research", source_url=url,
    )


def _cand(cid: str) -> Candidate:
    return Candidate(
        complex_id=cid, name=cid, approval_date=None, parking_ratio=None,
        parking_underground=None, household_count=None, lat=None, lng=None,
        source_url=None, transaction_count=0, price_min=None, price_max=None,
        representative_trade=None,
    )


def test_synthesize_none_when_no_facts() -> None:
    s = synthesize_floorplan([])
    assert s.bay is None and s.orientation is None and s.structure is None and s.sources == []


def test_synthesize_primary_highest_confidence_and_multisource() -> None:
    facts = [
        _fact(conf=0.3, url="https://a/1", bay=2),
        _fact(conf=0.6, url="https://b/2", bay=4, structure="타워형"),
    ]
    s = synthesize_floorplan(facts)
    assert s.bay == 4 and s.structure == "타워형" and s.confidence == 0.6
    assert len(s.sources) == 2


def test_synthesize_preserves_null_features() -> None:
    s = synthesize_floorplan([_fact(conf=0.4, url="https://a/1", bay=None, orientation=None)])
    assert s.bay is None and s.orientation is None and s.structure == "판상형"


def test_attach_reads_through_seed() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO complex (complex_id, name) VALUES ('A', '단지')")
    write_facts(conn, "A", "floorplan", [_fact(conf=0.5, url="https://a/1")], ttl=TTL, now=NOW)
    conn.commit()
    cand = _cand("A")
    attach_floorplan(conn, [cand], now=NOW)
    assert cand.floorplan is not None and cand.floorplan.bay == 3


def test_floorplan_is_never_a_ranking_signal() -> None:
    # 평면도는 표시 전용 — 조건 레지스트리 밖이라 랭킹 신호 불가. 부착해도 순서 불변.
    from app.search.criteria import REGISTRY

    assert "floorplan" not in REGISTRY  # 레지스트리 밖 → soft 랭킹 신호 불가
    assert {"gym", "pet", "criteria"} <= set(SoftSpec.model_fields)
    a, b = _cand("A"), _cand("B")
    a.floorplan = synthesize_floorplan([_fact(conf=0.9, url="https://a/1")])  # A만 평면도
    ranked = rank_candidates([a, b], SoftSpec())  # soft 전부 none
    assert [c.complex_id for c in ranked] == ["A", "B"]  # 입력순 불변(floorplan 무관)
