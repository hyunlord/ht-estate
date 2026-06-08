"""lazy 실추출기 (E1) — gym/pet 규율·환각drop·graceful-degrade·advisory·오케스트레이터 통합.

키리스: provider·fetcher를 mock 주입(라이브 호출 0). runner.enrich seam으로 hit/miss/write-back.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from app.enrich.extractors.gym import make_gym_extractor
from app.enrich.extractors.pet import make_pet_extractor
from app.enrich.fetcher import NullFetcher, SourceDoc
from app.enrich.live import live_extractors, name_resolver
from app.enrich.provider import ProviderError
from app.enrich.runner import enrich
from app.enrich.store import has_fresh, read_facts
from app.store.db import get_connection, init_db

NOW = datetime(2026, 6, 8, tzinfo=UTC)
TTL = timedelta(days=90)


class FakeProvider:
    def __init__(self, raw: str) -> None:
        self.raw = raw
        self.calls = 0

    def complete(self, system: str, user: str, /) -> str:
        self.calls += 1
        return self.raw


class DownProvider:
    def complete(self, system: str, user: str, /) -> str:
        raise ProviderError("down")


class FakeFetcher:
    def __init__(self, docs: list[SourceDoc]) -> None:
        self.docs = docs

    def fetch(self, query: str, *, kind: str) -> list[SourceDoc]:
        return list(self.docs)


_DOCS = [
    SourceDoc(source_type="web", source_url="http://a", text="단지 내 피트니스센터 운영"),
    SourceDoc(source_type="cafe", source_url="http://b", text="강아지 키우는 집 많아요"),
]


def _name(_cid: str) -> str:
    return "역삼자이"


# ── gym ──
def test_gym_extractor_produces_fact() -> None:
    raw = json.dumps(
        [{"source_url": "http://a", "has_gym": "yes", "evidence": "헬스장", "confidence": 0.8}]
    )
    ext = make_gym_extractor(FakeProvider(raw), FakeFetcher([_DOCS[0]]), _name)
    facts = ext("C1", "gym")
    assert len(facts) == 1
    assert json.loads(facts[0].value)["has_gym"] == "yes"
    assert facts[0].source_type == "web" and facts[0].source_url == "http://a"
    assert facts[0].confidence == 0.8


def test_gym_state_domain_enforced() -> None:
    # 도메인 밖 상태 → unknown(보수)
    raw = json.dumps([{"source_url": "http://a", "has_gym": "maybe", "confidence": 0.9}])
    facts = make_gym_extractor(FakeProvider(raw), FakeFetcher([_DOCS[0]]), _name)("C1", "gym")
    assert json.loads(facts[0].value)["has_gym"] == "unknown"


def test_anti_hallucination_drops_unknown_source() -> None:
    # LLM이 fetch 안 된 source_url을 지어내면 drop
    raw = json.dumps([{"source_url": "http://HALLUCINATED", "has_gym": "yes", "confidence": 1.0}])
    facts = make_gym_extractor(FakeProvider(raw), FakeFetcher([_DOCS[0]]), _name)("C1", "gym")
    assert facts == []


def test_confidence_clamped() -> None:
    raw = json.dumps([{"source_url": "http://a", "has_gym": "yes", "confidence": 5.0}])
    facts = make_gym_extractor(FakeProvider(raw), FakeFetcher([_DOCS[0]]), _name)("C1", "gym")
    assert facts[0].confidence == 1.0


# ── graceful-degrade (crash 금지·defer) ──
def test_graceful_provider_down() -> None:
    facts = make_gym_extractor(DownProvider(), FakeFetcher([_DOCS[0]]), _name)("C1", "gym")
    assert facts == []  # provider 다운 → defer(빈 결과)


def test_graceful_no_docs() -> None:
    facts = make_gym_extractor(FakeProvider("[]"), NullFetcher(), _name)("C1", "gym")
    assert facts == []  # 소스 없음 → miss


def test_graceful_malformed_llm() -> None:
    ext = make_gym_extractor(FakeProvider("not json"), FakeFetcher([_DOCS[0]]), _name)
    assert ext("C1", "gym") == []


def test_no_name_skips() -> None:
    ext = make_gym_extractor(FakeProvider("[]"), FakeFetcher([_DOCS[0]]), lambda _c: None)
    assert ext("C1", "gym") == []


# ── pet advisory ──
def test_pet_advisory_forces_confirm_and_caveats() -> None:
    # LLM이 confirm_with_office=false라 해도 advisory라 강제 true. caveats 보존. 다출처.
    raw = json.dumps(
        [
            {"source_url": "http://a", "pet_allowed": "conditional", "evidence": "소형견 가능",
             "caveats": ["소형견", "1마리"], "confirm_with_office": False, "confidence": 0.6},
            {"source_url": "http://b", "pet_allowed": "yes", "caveats": [], "confidence": 0.5},
        ]
    )
    docs = [SourceDoc(source_type="web", source_url="http://a", text="규약"),
            SourceDoc(source_type="cafe", source_url="http://b", text="카페")]
    facts = make_pet_extractor(FakeProvider(raw), FakeFetcher(docs), _name)("C1", "pet")
    assert len(facts) == 2  # 다출처 전부 보관
    a = next(json.loads(f.value) for f in facts if f.source_url == "http://a")
    assert a["pet_allowed"] == "conditional"
    assert a["caveats"] == ["소형견", "1마리"]
    assert all(json.loads(f.value)["confirm_with_office"] is True for f in facts)  # 전수 강제


def test_pet_unknown_state_conservative() -> None:
    raw = json.dumps([{"source_url": "http://a", "pet_allowed": "definitely", "confidence": 0.9}])
    docs = [SourceDoc(source_type="web", source_url="http://a", text="x")]
    facts = make_pet_extractor(FakeProvider(raw), FakeFetcher(docs), _name)("C1", "pet")
    assert json.loads(facts[0].value)["pet_allowed"] == "unknown"  # 도메인 밖 → 보수


# ── 오케스트레이터 통합(hit/miss/TTL/write-back) + 후보 한정 ──
@pytest.fixture
def db() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.executemany(
        "INSERT INTO complex (complex_id, name, property_type) VALUES (?, ?, 'rowhouse')",
        [("C1", "역삼자이"), ("C2", "다른빌라")],
    )
    conn.commit()
    return conn


def test_orchestrator_miss_then_hit(db: sqlite3.Connection) -> None:
    raw = json.dumps([{"source_url": "http://a", "has_gym": "yes", "confidence": 0.8}])
    provider = FakeProvider(raw)
    ext = make_gym_extractor(provider, FakeFetcher([_DOCS[0]]), name_resolver(db, ["C1"]))

    # 1차: miss → 추출 + write-back
    assert not has_fresh(db, "C1", "gym", now=NOW)
    enrich(db, ["C1"], "gym", ext, ttl=TTL, now=NOW)
    assert has_fresh(db, "C1", "gym", now=NOW)
    assert provider.calls == 1
    assert json.loads(read_facts(db, "C1", "gym", now=NOW)[0].value)["has_gym"] == "yes"

    # 2차: hit(TTL 유효) → 추출기 미호출
    enrich(db, ["C1"], "gym", ext, ttl=TTL, now=NOW + timedelta(days=1))
    assert provider.calls == 1  # 캐시 hit — 추가 호출 0

    # TTL 만료 후: 재추출
    enrich(db, ["C1"], "gym", ext, ttl=TTL, now=NOW + timedelta(days=200))
    assert provider.calls == 2


def test_candidate_limited_not_bulk(db: sqlite3.Connection) -> None:
    # 주어진 후보(C1)만 추출 — C2(미요청)는 무사실(대량 추출 아님)
    raw = json.dumps([{"source_url": "http://a", "has_gym": "yes", "confidence": 0.7}])
    ext = make_gym_extractor(FakeProvider(raw), FakeFetcher([_DOCS[0]]), name_resolver(db, ["C1"]))
    enrich(db, ["C1"], "gym", ext, ttl=TTL, now=NOW)
    assert has_fresh(db, "C1", "gym", now=NOW)
    assert not has_fresh(db, "C2", "gym", now=NOW)  # 후보 밖 → 미추출


# ── live seam ──
def test_live_extractors_none_when_unconfigured(
    db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ENRICH_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("ENRICH_LLM_MODEL", raising=False)
    assert live_extractors(db, ["C1"]) is None  # 미구성 → stub 유지(현 동작 불변)


def test_live_extractors_built_with_provider(db: sqlite3.Connection) -> None:
    ext = live_extractors(db, ["C1"], provider=FakeProvider("[]"), fetcher=NullFetcher())
    assert ext is not None and set(ext) == {"gym", "pet"}
