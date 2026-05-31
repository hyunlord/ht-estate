"""gym 시드 로더 — 적재·멱등·재개 skip·value 형태 (키리스)."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from load_gym_seed import ATTRIBUTE, load_seed, load_seed_records  # noqa: E402

from app.enrich.store import read_facts  # noqa: E402
from app.store.db import get_connection, init_db  # noqa: E402

NOW = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
TTL = timedelta(days=90)
SEED = Path(__file__).resolve().parents[1] / "data" / "seeds" / "gym_gangnam.jsonl"


def _db(*complex_ids: str) -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.executemany(
        "INSERT INTO complex (complex_id, name) VALUES (?, ?)", [(c, c) for c in complex_ids]
    )
    conn.commit()
    return conn


_RECORDS = [
    {
        "complex_id": "C1", "has_gym": "yes", "in_complex": True, "evidence": "단지 내 피트니스",
        "confidence": 0.85, "source_type": "official", "source_url": "https://x.example/1",
    },
    {
        "complex_id": "C1", "has_gym": "yes", "in_complex": True, "evidence": "블로그 후기",
        "confidence": 0.6, "source_type": "blog", "source_url": "https://b.example/2",
    },
    {
        "complex_id": "C2", "has_gym": "no", "in_complex": False, "evidence": "인근 상업뿐",
        "confidence": 0.6, "source_type": "agent_research", "source_url": "urn:x:C2",
    },
]


def test_load_writes_facts_with_value_shape() -> None:
    conn = _db("C1", "C2")
    stats = load_seed(conn, _RECORDS, ttl=TTL, now=NOW)
    assert stats == {"loaded": 3, "skipped": 0, "complexes": 2}

    facts = read_facts(conn, "C1", ATTRIBUTE, now=NOW)
    assert len(facts) == 2  # 출처당 1행
    parsed = json.loads(facts[0].value)
    assert parsed["has_gym"] in ("yes", "no", "unknown")
    assert "evidence" in parsed


def test_load_is_idempotent() -> None:
    conn = _db("C1", "C2")
    load_seed(conn, _RECORDS, ttl=TTL, now=NOW)
    # 재실행: fresh 있으면 단지 skip(재개)
    stats = load_seed(conn, _RECORDS, ttl=TTL, now=NOW)
    assert stats["loaded"] == 0
    assert stats["skipped"] == 2
    # 행 수 불변
    assert read_facts(conn, "C1", ATTRIBUTE, now=NOW)[0] is not None


def test_resume_loads_only_new_complexes() -> None:
    conn = _db("C1", "C2")
    load_seed(conn, _RECORDS[:1], ttl=TTL, now=NOW)  # C1만 먼저
    # 전체 재실행: C1 skip, C2만 신규 적재
    stats = load_seed(conn, _RECORDS, ttl=TTL, now=NOW)
    assert stats["skipped"] == 1  # C1
    assert stats["loaded"] == 1  # C2 1건


def test_real_seed_file_parses_and_loads() -> None:
    # 실제 시드 파일이 유효하고 적재되는지(차단 도메인 출처 없음 단언 포함)
    records = load_seed_records(SEED)
    assert len(records) >= 1
    blocked = ("naver.com", "hogangnono", "asil.kr")
    for r in records:
        assert r["has_gym"] in ("yes", "no", "unknown")
        url = str(r["source_url"])
        assert not any(b in url for b in blocked), f"차단 도메인 출처: {url}"

    conn = _db(*[str(r["complex_id"]) for r in records])
    stats = load_seed(conn, records, ttl=TTL, now=NOW)
    assert stats["loaded"] >= 1
