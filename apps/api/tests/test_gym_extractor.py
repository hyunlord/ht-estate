"""gym 추출기 — 단지내/상업 구별·confidence·다출처·no-signal·차단·runner 결합(mock, 키리스)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from app.enrich.extractors.gym import GymExtractor
from app.enrich.runner import enrich
from app.enrich.search import SearchResult
from app.store.db import get_connection, init_db

NOW = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
TTL = timedelta(days=30)


def _db(name: str = "역삼자이", addr: str = "서울특별시 강남구 언주로 420") -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.execute(
        "INSERT INTO complex (complex_id, name, road_addr) VALUES ('C1', ?, ?)", (name, addr)
    )
    conn.commit()
    return conn


def _search(*results: SearchResult):  # type: ignore[no-untyped-def]
    return lambda _q: list(results)


def _fetch_ok(_url: str) -> str | None:
    return "단지 내 입주민 피트니스 센터 운영 안내"


def _llm(response: dict[str, Any]):  # type: ignore[no-untyped-def]
    return lambda _system, _user: response


def _resp(has_gym: str, in_complex: bool, conf: float) -> dict[str, Any]:
    return {"has_gym": has_gym, "in_complex": in_complex, "evidence": "근거", "confidence": conf}


def _verdict(fact) -> str:  # type: ignore[no-untyped-def]
    return json.loads(fact.value)["has_gym"]


def test_in_complex_gym_yields_yes() -> None:
    conn = _db()
    ext = GymExtractor(
        conn,
        _search(SearchResult(url="https://blog.example/1", title="후기", source_kind="blog")),
        _fetch_ok,
        _llm(_resp("yes", True, 0.8)),
    )
    facts = ext("C1", "gym")
    assert len(facts) == 1
    assert _verdict(facts[0]) == "yes"
    assert facts[0].confidence == round(0.6 * 0.8, 3)  # blog 가중 0.6 × LLM 0.8


def test_commercial_gym_demoted_to_unknown() -> None:
    # R1 핵심: 인근 상업 헬스장(in_complex=false)은 yes를 unknown으로 강등
    conn = _db()
    ext = GymExtractor(
        conn,
        _search(SearchResult(url="https://news.example/2", title="기사", source_kind="news")),
        _fetch_ok,
        _llm(_resp("yes", False, 0.9)),  # in_complex=False → 인근 상업
    )
    facts = ext("C1", "gym")
    assert _verdict(facts[0]) == "unknown"  # 단지 내부 아님 → 강등


def test_source_quality_weights_confidence() -> None:
    conn = _db()
    # 공식홈(official 1.0) × LLM 0.9 = 0.9
    ext = GymExtractor(
        conn,
        _search(SearchResult(url="https://official.example", title="공식", source_kind="official")),
        _fetch_ok,
        _llm({"has_gym": "yes", "in_complex": True, "evidence": "헬스장", "confidence": 0.9}),
    )
    assert ext("C1", "gym")[0].confidence == round(1.0 * 0.9, 3)


def test_multiple_sources_multiple_facts() -> None:
    conn = _db()
    ext = GymExtractor(
        conn,
        _search(
            SearchResult(url="https://blog.example/a", title="a", source_kind="blog"),
            SearchResult(url="https://cafe.example/b", title="b", source_kind="cafe"),
        ),
        _fetch_ok,
        _llm({"has_gym": "yes", "in_complex": True, "evidence": "헬스장", "confidence": 0.7}),
    )
    facts = ext("C1", "gym")
    assert len(facts) == 2
    assert {f.source_type for f in facts} == {"blog", "cafe"}


def test_blocked_domain_skipped() -> None:
    # R1 legal: 네이버/호갱노노/아실 미스크레이프 → 신호 없음 → UNKNOWN sentinel
    conn = _db()
    ext = GymExtractor(
        conn,
        _search(SearchResult(url="https://m.land.naver.com/x", title="네이버", source_kind="web")),
        _fetch_ok,
        _llm({"has_gym": "yes", "in_complex": True, "evidence": "x", "confidence": 0.9}),
    )
    facts = ext("C1", "gym")
    assert len(facts) == 1
    assert facts[0].source_type == "none"  # 차단 → no-signal sentinel
    assert _verdict(facts[0]) == "unknown"


def test_no_signal_yields_unknown_sentinel() -> None:
    conn = _db()
    ext = GymExtractor(conn, _search(), _fetch_ok, _llm({}))  # 검색 0건
    facts = ext("C1", "gym")
    assert len(facts) == 1
    assert _verdict(facts[0]) == "unknown"
    assert facts[0].confidence == 0.1
    assert facts[0].source_url.startswith("urn:ht-estate:gym:no-signal:")


def test_fetch_failure_is_graceful() -> None:
    conn = _db()
    ext = GymExtractor(
        conn,
        _search(SearchResult(url="https://blog.example/x", title="x", source_kind="blog")),
        lambda _url: None,  # fetch 실패
        _llm({"has_gym": "yes", "in_complex": True, "evidence": "x", "confidence": 0.9}),
    )
    facts = ext("C1", "gym")
    assert facts[0].source_type == "none"  # fetch 실패 → 신호 없음 → sentinel


def test_wrong_attribute_returns_empty() -> None:
    conn = _db()
    ext = GymExtractor(conn, _search(), _fetch_ok, _llm({}))
    assert ext("C1", "pet_allowed") == []


def test_combines_with_p1_1_runner() -> None:
    # P1-1 runner에 주입 → write-back + 캐시
    conn = _db()
    ext = GymExtractor(
        conn,
        _search(SearchResult(url="https://blog.example/1", title="후기", source_kind="blog")),
        _fetch_ok,
        _llm(_resp("yes", True, 0.8)),
    )
    result = enrich(conn, ["C1"], "gym", ext, ttl=TTL, now=NOW)
    assert len(result["C1"]) == 1
    assert _verdict(result["C1"][0]) == "yes"
    # 재실행: fresh hit → 추출기 미호출(검색 호출 0)
    calls = {"n": 0}

    def counting_search(_q: str) -> list[SearchResult]:
        calls["n"] += 1
        return []

    ext2 = GymExtractor(conn, counting_search, _fetch_ok, _llm({}))
    enrich(conn, ["C1"], "gym", ext2, ttl=TTL, now=NOW)
    assert calls["n"] == 0  # 캐시 hit
