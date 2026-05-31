"""pet 시드 로더 — 적재·멱등·재개 skip·value 형태·규율(키리스).

규율 단언: pet_allowed 도메인 · confirm_with_office 전부 true · caveats 보존 ·
차단 도메인(naver/hogangnono/asil) 출처 0 · 보수적 confidence(카페 단독 yes 금지).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from load_pet_seed import ATTRIBUTE, load_seed, load_seed_records  # noqa: E402

from app.enrich.store import read_facts  # noqa: E402
from app.store.db import get_connection, init_db  # noqa: E402

NOW = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
TTL = timedelta(days=90)
SEED = Path(__file__).resolve().parents[1] / "data" / "seeds" / "pet_gangnam.jsonl"
STATES = ("yes", "conditional", "no", "unknown")


def _db(*complex_ids: str) -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.executemany(
        "INSERT INTO complex (complex_id, name) VALUES (?, ?)", [(c, c) for c in complex_ids]
    )
    conn.commit()
    return conn


_RECORDS: list[dict[str, object]] = [
    {
        "complex_id": "P1", "pet_allowed": "conditional",
        "evidence": "관리규약상 허용하되 제한", "caveats": ["견종 제한", "마릿수 제한"],
        "confidence": 0.6, "confirm_with_office": True,
        "source_type": "official", "source_url": "https://x.example/rule",
    },
    {
        "complex_id": "P2", "pet_allowed": "unknown",
        "evidence": "공개 신호 없음", "caveats": [],
        "confidence": 0.2, "confirm_with_office": True,
        "source_type": "agent_research", "source_url": "urn:x:P2",
    },
]


def test_load_writes_value_shape_with_pet_fields() -> None:
    conn = _db("P1", "P2")
    stats = load_seed(conn, _RECORDS, ttl=TTL, now=NOW)
    assert stats == {"loaded": 2, "skipped": 0, "complexes": 2}

    facts = read_facts(conn, "P1", ATTRIBUTE, now=NOW)
    parsed = json.loads(facts[0].value)
    assert parsed["pet_allowed"] in STATES
    assert parsed["caveats"] == ["견종 제한", "마릿수 제한"]  # 제한 단서 보존
    assert parsed["confirm_with_office"] is True  # 관리사무소 확인 플래그


def test_confirm_with_office_defaults_true_when_missing() -> None:
    # 누락 시 보수적으로 true(잘못된 "확인 불필요" 방지).
    conn = _db("P3")
    rec = [{
        "complex_id": "P3", "pet_allowed": "yes", "evidence": "e", "caveats": [],
        "confidence": 0.5, "source_type": "official", "source_url": "https://y/1",
    }]
    load_seed(conn, rec, ttl=TTL, now=NOW)
    parsed = json.loads(read_facts(conn, "P3", ATTRIBUTE, now=NOW)[0].value)
    assert parsed["confirm_with_office"] is True


def test_load_is_idempotent_and_resumable() -> None:
    conn = _db("P1", "P2")
    load_seed(conn, _RECORDS, ttl=TTL, now=NOW)
    stats = load_seed(conn, _RECORDS, ttl=TTL, now=NOW)  # 재실행 → fresh skip
    assert stats["loaded"] == 0
    assert stats["skipped"] == 2

    conn2 = _db("P1", "P2")
    load_seed(conn2, _RECORDS[:1], ttl=TTL, now=NOW)  # P1만
    stats2 = load_seed(conn2, _RECORDS, ttl=TTL, now=NOW)  # P1 skip, P2 신규
    assert stats2["skipped"] == 1
    assert stats2["loaded"] == 1


def test_real_seed_parses_and_respects_discipline() -> None:
    records = load_seed_records(SEED)
    assert len(records) >= 1
    blocked = ("naver.com", "hogangnono", "asil.kr")
    for r in records:
        assert r["pet_allowed"] in STATES, f"도메인 밖 pet_allowed: {r['pet_allowed']}"
        assert r["confirm_with_office"] is True, "confirm_with_office 누락/false"
        assert isinstance(r["caveats"], list)
        url = str(r["source_url"])
        assert not any(b in url for b in blocked), f"차단 도메인 출처: {url}"
        # 보수성: 카페/blog 단독 출처는 yes 단정 금지
        if r["source_type"] in ("cafe", "blog") and r["pet_allowed"] == "yes":
            raise AssertionError(f"약한 출처 단독 yes 단정: {r['complex_id']}")
        # 약한 출처(agent_research/cafe/blog)는 confidence 보수적(<= 0.5)
        if r["source_type"] in ("agent_research", "cafe", "blog"):
            conf = float(str(r["confidence"]))
            assert conf <= 0.5, f"약한 출처 confidence 과대: {r['complex_id']}"

    conn = _db(*[str(r["complex_id"]) for r in records])
    stats = load_seed(conn, records, ttl=TTL, now=NOW)
    assert stats["loaded"] >= 1


def test_real_seed_has_no_duplicate_complex_source_pairs() -> None:
    # PK(complex_id, attribute, source_url) 충돌로 묵음 손실 방지 — 시드 내 중복 0.
    records = load_seed_records(SEED)
    pairs = [(r["complex_id"], r["source_url"]) for r in records]
    assert len(pairs) == len(set(pairs)), "시드에 (complex_id, source_url) 중복 존재"
