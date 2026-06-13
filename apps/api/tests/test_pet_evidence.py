"""pet-evidence — pet doc 교차검증(공유 doc_verify 패턴·C86 게이트+gemma) + advisory 결합.

키리스(provider·fetcher mock). 검증: thin config 인스턴스화(쿼리/verify_system)·건물게이트 재사용
(딴 건물·노이즈 reject)·gemma 판정(allowed/not/unclear + 견종/무게 caveats)·**advisory 바닥**
(confirm_with_office 전수 True·하드 yes 단정 X)·결합+missing=keep·온디맨드 트리거 디커플링·enrich가
pet_verified만 write(좌표/canonical/review_chunk 불변)·graceful.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from app.enrich.extractors.doc_verify import WEB_VERIFIED
from app.enrich.extractors.pet_verify import PET_VERIFIED, make_pet_verify_extractor
from app.enrich.fetcher import SourceDoc
from app.enrich.ondemand import PENDING, OnDemandEnricher
from app.enrich.provider import ProviderError
from app.enrich.store import EnrichmentFact, read_facts, write_facts
from app.search.pet import ATTRIBUTE as PET
from app.search.pet import synthesize_pet
from app.store.db import get_connection, init_db

NOW = datetime(2026, 6, 13, tzinfo=UTC)
NAME = "역삼자이"
TARGET = (NAME, "강남구 역삼동", ["역삼동", "강남구"])


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


def _resolve(_cid: str):  # type: ignore[no-untyped-def]
    return TARGET


def _verdict(url: str, verdict: str, caveats: list[str] | None = None, conf: float = 0.8) -> str:
    return json.dumps([{"source_url": url, "verdict": verdict, "evidence": "관리규약 언급",
                        "caveats": caveats or [], "confidence": conf}])


# ── thin config 인스턴스화: 건물게이트 재사용 + gemma 판정 + advisory 강제 ──
def test_pet_verify_allowed_with_caveats() -> None:
    docs = [SourceDoc(source_type="cafe", source_url="http://a",
                      text="역삼자이 반려동물 관리규약 허용")]
    ext = make_pet_verify_extractor(
        FakeProvider(_verdict("http://a", "allowed", ["20kg 이하", "맹견 제외"])),
        FakeFetcher(docs), _resolve)
    facts = ext("C1", PET_VERIFIED)
    assert len(facts) == 1
    v = json.loads(facts[0].value)
    assert v["pet_allowed"] == "yes"
    assert v["confirm_with_office"] is True  # ★ advisory 전수(LLM 무관 강제)
    assert "20kg 이하" in v["caveats"]  # 견종/무게 단서 보존
    assert facts[0].source_type == WEB_VERIFIED


def test_pet_verify_verdict_mapping() -> None:
    docs = [SourceDoc(source_type="cafe", source_url="http://a", text="역삼자이 반려동물 관련")]
    for verdict, expect in [("allowed", "yes"), ("not-allowed", "no"), ("unclear", "unknown"),
                            ("garbage", "unknown")]:
        ext = make_pet_verify_extractor(FakeProvider(_verdict("http://a", verdict)),
                                        FakeFetcher(docs), _resolve)
        facts = ext("C1", PET_VERIFIED)
        assert facts and json.loads(facts[0].value)["pet_allowed"] == expect
        assert json.loads(facts[0].value)["confirm_with_office"] is True  # 어떤 판정도 advisory


def test_pet_verify_rejects_other_building_via_gate() -> None:
    docs = [SourceDoc(source_type="cafe", source_url="http://x", text="딴단지 반려동물 가능")]
    prov = FakeProvider(_verdict("http://x", "allowed"))
    ext = make_pet_verify_extractor(prov, FakeFetcher(docs), _resolve)
    assert ext("C1", PET_VERIFIED) == []
    assert prov.calls == 0  # 게이트서 전부 reject → LLM 미호출


def test_pet_verify_drops_petshop_ad_noise() -> None:
    docs = [SourceDoc(source_type="cafe", source_url="http://n", text="역삼자이 반려동물 분양")]
    ext = make_pet_verify_extractor(FakeProvider(_verdict("http://n", "allowed")),
                                    FakeFetcher(docs), _resolve)
    assert ext("C1", PET_VERIFIED) == []  # 분양/매물 노이즈 drop


def test_pet_verify_graceful_provider_down() -> None:
    docs = [SourceDoc(source_type="cafe", source_url="http://a", text="역삼자이 반려동물")]
    ext = make_pet_verify_extractor(DownProvider(), FakeFetcher(docs), _resolve)
    assert ext("C1", PET_VERIFIED) == []  # defer·crash 0


# ── synthesize_pet 결합 + advisory ──
def _seed(pet_allowed: str, conf: float = 0.7, src: str = WEB_VERIFIED,
          url: str = "http://blog/1", caveats: list[str] | None = None) -> EnrichmentFact:
    return EnrichmentFact(
        value=json.dumps({"pet_allowed": pet_allowed, "evidence": "관리규약",
                          "caveats": caveats or [], "confirm_with_office": True},
                         ensure_ascii=False),
        confidence=conf, source_type=src, source_url=url)


def test_synthesize_pet_always_advisory() -> None:
    # ★ allowed여도 confirm_with_office True(하드 ✓ 아님·관리사무소 확인). 견종 단서 보존.
    s = synthesize_pet([_seed("yes", caveats=["소형견만"])])
    assert s.pet_allowed == "yes" and s.confirm_with_office is True
    assert "소형견만" in s.caveats


def test_synthesize_pet_combines_seed_and_verified() -> None:
    # 레거시 시드 + pet_verified 결합(다출처 provenance).
    s = synthesize_pet([_seed("conditional", conf=0.5, src="agent_research", url="urn:a"),
                        _seed("yes", conf=0.85, url="http://blog/1")])
    assert len(s.sources) == 2 and s.confirm_with_office is True


def test_synthesize_pet_missing_keep_unclear() -> None:
    # unclear만 → unknown(미확인·없는 yes/no 날조 0).
    assert synthesize_pet([_seed("unknown")]).pet_allowed == "unknown"


def test_synthesize_pet_none_when_empty() -> None:
    s = synthesize_pet([])
    assert s.pet_allowed == "none" and s.confirm_with_office is True


# ── 온디맨드 트리거 디커플링 + write 격리 ──
def _db() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.execute(
        "INSERT INTO complex (complex_id, name, property_type, sigungu, dong, lat, lng) "
        "VALUES ('C1', '역삼자이', 'apartment', '강남구', '역삼동', 37.5, 127.0)")
    conn.commit()
    return conn


def test_seed_pet_does_not_short_circuit_doc_verify() -> None:
    # ★ 기존 'pet' 사실이 있어도 'pet_verified'(별도 속성) doc 검증은 트리거(둘 다 확보).
    conn = _db()
    write_facts(conn, "C1", PET, [_seed("conditional", src="agent_research", url="urn:a")],
                ttl=timedelta(days=90), now=NOW)
    submitted: list = []
    enricher = OnDemandEnricher(
        provider=FakeProvider("[]"), fetcher=FakeFetcher([]),
        submit=lambda fn: submitted.append(fn))  # 제출만 캡처(conn 보존)
    state, _ = enricher.status(conn, "C1", PET_VERIFIED, now=NOW)
    assert state == PENDING and submitted  # 시드 'pet' 있어도 pet_verified miss → 트리거


def test_enrich_writes_pet_verified_only_canonical_unchanged() -> None:
    from app.enrich.runner import enrich
    conn = _db()
    conn.execute('INSERT INTO "transaction" (txn_id, complex_id, deal_date) VALUES (?,?,?)',
                 ("t1", "C1", "2026-05-01"))
    conn.commit()
    before = (
        conn.execute("SELECT COUNT(*) FROM complex").fetchone()[0],
        conn.execute('SELECT COUNT(*) FROM "transaction"').fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM review_chunk").fetchone()[0],
        conn.execute("SELECT lat||','||lng FROM complex WHERE complex_id='C1'").fetchone()[0],
    )
    docs = [SourceDoc(source_type="cafe", source_url="http://a", text="역삼자이 반려동물 허용")]
    ext = make_pet_verify_extractor(FakeProvider(_verdict("http://a", "allowed")),
                                    FakeFetcher(docs), _resolve)
    enrich(conn, ["C1"], PET_VERIFIED, ext, ttl=timedelta(days=90), now=NOW)
    after = (
        conn.execute("SELECT COUNT(*) FROM complex").fetchone()[0],
        conn.execute('SELECT COUNT(*) FROM "transaction"').fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM review_chunk").fetchone()[0],
        conn.execute("SELECT lat||','||lng FROM complex WHERE complex_id='C1'").fetchone()[0],
    )
    assert before == after  # canonical/좌표/review_chunk 불변(pet_verified 사실만)
    assert len(read_facts(conn, "C1", PET_VERIFIED, now=NOW)) == 1
