"""review_summary 시드 로더 (P3-1) — value=JSON{summary,points}·멱등·다출처 (키리스)."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.enrich.store import read_facts
from app.store.db import get_connection, init_db

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from load_review_seed import load_seed  # noqa: E402

NOW = datetime(2026, 6, 1, tzinfo=UTC)
TTL = timedelta(days=90)


def _conn() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO complex (complex_id, name) VALUES ('A', '단지A')")
    conn.commit()
    return conn


def _rec(url: str, summary: str = "조용함", conf: float = 0.4) -> dict[str, object]:
    return {
        "complex_id": "A", "summary": summary, "points": ["조용", "주차 여유"],
        "confidence": conf, "source_type": "youtube", "source_url": url,
    }


def test_load_writes_summary_value_json() -> None:
    conn = _conn()
    stats = load_seed(conn, [_rec("https://youtube.com/a")], ttl=TTL, now=NOW)
    assert stats["loaded"] == 1
    facts = read_facts(conn, "A", "review_summary", now=NOW)
    assert len(facts) == 1
    data = json.loads(facts[0].value)
    assert data["summary"] == "조용함" and data["points"] == ["조용", "주차 여유"]
    assert facts[0].source_url == "https://youtube.com/a"


def test_load_keeps_multiple_sources() -> None:
    conn = _conn()
    load_seed(conn, [_rec("https://youtube.com/a"), _rec("https://tistory.com/b")],
              ttl=TTL, now=NOW)
    facts = read_facts(conn, "A", "review_summary", now=NOW)
    assert len(facts) == 2  # 출처별 다중 행(§4)


def test_load_is_idempotent_resume() -> None:
    conn = _conn()
    load_seed(conn, [_rec("https://youtube.com/a")], ttl=TTL, now=NOW)
    again = load_seed(conn, [_rec("https://youtube.com/a")], ttl=TTL, now=NOW)
    assert again["skipped"] == 1  # fresh 있으면 skip(재개)
