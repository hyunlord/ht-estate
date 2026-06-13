"""gym-evidence — gym 증거 doc 교차검증(C86 건물게이트+gemma) + Kakao 위치와 결합.

키리스(provider·fetcher mock). 검증: 건물게이트 재사용(딴 건물·노이즈 reject)·gemma 구조화 판정
(confirmed/no/unclear)·결합 로직(kakao+doc/kakao만/doc만/없음·no-false-flip·missing=keep)·
온디맨드 트리거 디커플링(kakao 'gym' 사실이 doc 검증 단락 안 함)·enrich가 gym_verified만 write
(좌표/canonical/review_chunk 불변)·graceful.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from app.enrich.extractors.gym_verify import (
    GYM_VERIFIED,
    WEB_VERIFIED,
    make_gym_verify_extractor,
)
from app.enrich.fetcher import SourceDoc
from app.enrich.ondemand import PENDING, OnDemandEnricher
from app.enrich.provider import ProviderError
from app.enrich.store import EnrichmentFact, read_facts, write_facts
from app.search.gym import ATTRIBUTE as GYM
from app.search.gym import synthesize_gym
from app.search.gym_kakao import GYM_CONFIDENCE, gym_fact_value
from app.search.gym_kakao import SOURCE_TYPE as KAKAO_LOCAL
from app.store.db import get_connection, init_db

NOW = datetime(2026, 6, 13, tzinfo=UTC)
NAME = "역삼자이"
# (name, region_label, region_tokens) — 역삼자이는 distinctive(4자) → region 강등(동 불요).
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


def _verdict(url: str, verdict: str, conf: float = 0.8) -> str:
    return json.dumps([{"source_url": url, "verdict": verdict,
                        "evidence": "단지 내 헬스장 운영", "confidence": conf}])


# ── 추출기: 건물게이트 재사용(딴 건물·노이즈 reject) + gemma 검증 ──
def test_verify_keeps_building_gym_confirmed() -> None:
    docs = [SourceDoc(source_type="web", source_url="http://a", text="역삼자이 단지 내 헬스장")]
    ext = make_gym_verify_extractor(FakeProvider(_verdict("http://a", "confirmed")),
                                    FakeFetcher(docs), _resolve)
    facts = ext("C1", GYM_VERIFIED)
    assert len(facts) == 1
    assert json.loads(facts[0].value)["has_gym"] == "yes"
    assert facts[0].source_type == WEB_VERIFIED  # doc-LLM 검증 마킹(결합서 kakao와 구분)


def test_verify_rejects_other_building_via_gate() -> None:
    # 딴 단지 doc(역삼자이 코어 미포함) → C86 게이트가 LLM 전 drop → 무결과(defer).
    docs = [SourceDoc(source_type="web", source_url="http://x", text="딴단지 헬스장 좋아요")]
    prov = FakeProvider(_verdict("http://x", "confirmed"))
    ext = make_gym_verify_extractor(prov, FakeFetcher(docs), _resolve)
    assert ext("C1", GYM_VERIFIED) == []
    assert prov.calls == 0  # 게이트서 전부 reject → LLM 미호출(토큰 절약)


def test_verify_drops_noise_ad() -> None:
    # 단지명 맞아도 경매/인테리어 광고 → 노이즈 필터 drop.
    docs = [SourceDoc(source_type="cafe", source_url="http://n", text="역삼자이 헬스장 경매")]
    ext = make_gym_verify_extractor(FakeProvider(_verdict("http://n", "confirmed")),
                                    FakeFetcher(docs), _resolve)
    assert ext("C1", GYM_VERIFIED) == []


def test_verify_verdict_mapping() -> None:
    docs = [SourceDoc(source_type="web", source_url="http://a", text="역삼자이 헬스장 관련")]
    for verdict, expect in [("confirmed", "yes"), ("no", "no"), ("unclear", "unknown"),
                            ("garbage", "unknown")]:
        ext = make_gym_verify_extractor(FakeProvider(_verdict("http://a", verdict)),
                                        FakeFetcher(docs), _resolve)
        facts = ext("C1", GYM_VERIFIED)
        assert facts and json.loads(facts[0].value)["has_gym"] == expect


def test_verify_graceful_provider_down() -> None:
    docs = [SourceDoc(source_type="web", source_url="http://a", text="역삼자이 헬스장")]
    ext = make_gym_verify_extractor(DownProvider(), FakeFetcher(docs), _resolve)
    assert ext("C1", GYM_VERIFIED) == []  # defer·crash 0


# ── 결합 로직: Kakao 위치 + doc 검증 ──
def _kakao() -> EnrichmentFact:
    return EnrichmentFact(value=gym_fact_value({"place_name": "스포애니", "distance_m": 13}),
                          confidence=GYM_CONFIDENCE, source_type=KAKAO_LOCAL,
                          source_url="http://place.map.kakao.com/1")


def _doc(has_gym: str, conf: float = 0.8, url: str = "http://blog/1") -> EnrichmentFact:
    return EnrichmentFact(
        value=json.dumps({"has_gym": has_gym, "evidence": "단지 헬스장 후기"}, ensure_ascii=False),
        confidence=conf, source_type=WEB_VERIFIED, source_url=url)


def test_combine_kakao_and_doc_both_yes() -> None:
    s = synthesize_gym([_kakao(), _doc("yes")])
    assert s.has_gym == "yes"
    assert s.evidence and "스포애니" in s.evidence and "헬스장" in s.evidence  # 위치+증거
    assert s.confidence is not None and s.confidence > GYM_CONFIDENCE  # 일치→부스트
    assert len(s.sources) == 2


def test_combine_kakao_only() -> None:
    s = synthesize_gym([_kakao()])
    assert s.has_gym == "yes" and (s.evidence or "").find("스포애니") >= 0
    assert s.confidence == GYM_CONFIDENCE


def test_combine_doc_only_yes() -> None:
    # Kakao POI 무(타이트 반경 놓침) + doc 확인 → gym likely(doc 출처).
    s = synthesize_gym([_doc("yes", conf=0.82)])
    assert s.has_gym == "yes" and (s.evidence or "").find("헬스장") >= 0
    assert s.confidence == 0.82


def test_combine_no_false_flip() -> None:
    # ★ doc 'no'/'unclear'가 Kakao 'yes'를 절대 ✗로 안 뒤집음(no-false-flip).
    assert synthesize_gym([_kakao(), _doc("no")]).has_gym == "yes"
    assert synthesize_gym([_kakao(), _doc("unknown")]).has_gym == "yes"


def test_combine_missing_keep_doc_unclear() -> None:
    # doc unclear만(Kakao 무) → unknown(없는 gym 단정 안 함·missing=keep).
    assert synthesize_gym([_doc("unknown")]).has_gym == "unknown"


def test_combine_doc_no_only() -> None:
    assert synthesize_gym([_doc("no", conf=0.7)]).has_gym == "no"


def test_combine_none_when_empty() -> None:
    s = synthesize_gym([])
    assert s.has_gym == "none" and s.confidence is None


# ── 온디맨드 트리거 디커플링 + write 격리 ──
def _db() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.execute(
        "INSERT INTO complex (complex_id, name, property_type, sigungu, dong, lat, lng) "
        "VALUES ('C1', '역삼자이', 'apartment', '강남구', '역삼동', 37.5, 127.0)")
    conn.commit()
    return conn


def test_kakao_fact_does_not_short_circuit_doc_verify() -> None:
    # ★ Kakao 'gym' 사실이 있어도 'gym_verified'(별도 속성) doc 검증은 트리거(둘 다 확보).
    conn = _db()
    write_facts(conn, "C1", GYM, [_kakao()], ttl=timedelta(days=365), now=NOW)
    submitted: list = []
    enricher = OnDemandEnricher(
        provider=FakeProvider("[]"), fetcher=FakeFetcher([]),
        submit=lambda fn: submitted.append(fn),  # 제출만 캡처(실행 안 함 → conn 보존)
    )
    state, _ = enricher.status(conn, "C1", GYM_VERIFIED, now=NOW)
    assert state == PENDING and submitted  # kakao 'gym' 있어도 gym_verified miss → 트리거 제출


def test_verify_extractor_writes_gym_verified_via_enrich() -> None:
    # 추출기→enrich 적재 경로: gym_verified 속성에 web_verified 사실 write(결합용).
    from app.enrich.runner import enrich
    conn = _db()
    docs = [SourceDoc(source_type="web", source_url="http://a", text="역삼자이 단지 헬스장")]
    ext = make_gym_verify_extractor(FakeProvider(_verdict("http://a", "confirmed")),
                                    FakeFetcher(docs), _resolve)
    enrich(conn, ["C1"], GYM_VERIFIED, ext, ttl=timedelta(days=90), now=NOW)
    ver = read_facts(conn, "C1", GYM_VERIFIED, now=NOW)
    assert len(ver) == 1 and ver[0].source_type == WEB_VERIFIED


def test_enrich_writes_gym_verified_only_canonical_unchanged() -> None:
    # gym_verified만 write — complex/txn/review_chunk·좌표 불변(지문/counts 불변 바닥).
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
    write_facts(conn, "C1", GYM_VERIFIED, [_doc("yes")], ttl=timedelta(days=90), now=NOW)
    after = (
        conn.execute("SELECT COUNT(*) FROM complex").fetchone()[0],
        conn.execute('SELECT COUNT(*) FROM "transaction"').fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM review_chunk").fetchone()[0],
        conn.execute("SELECT lat||','||lng FROM complex WHERE complex_id='C1'").fetchone()[0],
    )
    assert before == after  # canonical/좌표/review_chunk 불변(gym_verified 사실만)
