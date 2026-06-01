"""floorplan 시드 로더 (P3-2) — value=JSON{bay,orientation,structure}·멱등·다출처 (키리스)."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.enrich.store import read_facts
from app.store.db import get_connection, init_db

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from load_floorplan_seed import load_seed  # noqa: E402

NOW = datetime(2026, 6, 1, tzinfo=UTC)
TTL = timedelta(days=90)


def _conn() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO complex (complex_id, name) VALUES ('A', '단지A')")
    conn.commit()
    return conn


def _rec(url: str, *, bay: object = 3, orientation: object = "남향") -> dict[str, object]:
    return {
        "complex_id": "A", "bay": bay, "orientation": orientation, "structure": "판상형",
        "evidence": "전면 3실", "confidence": 0.5, "source_type": "agent_research",
        "source_url": url,
    }


def test_load_writes_feature_value_json() -> None:
    conn = _conn()
    stats = load_seed(conn, [_rec("https://x/1")], ttl=TTL, now=NOW)
    assert stats["loaded"] == 1
    facts = read_facts(conn, "A", "floorplan", now=NOW)
    data = json.loads(facts[0].value)
    assert data["bay"] == 3 and data["orientation"] == "남향" and data["structure"] == "판상형"
    assert data["evidence"] == "전면 3실"


def test_load_preserves_null_features() -> None:
    conn = _conn()
    load_seed(conn, [_rec("https://x/1", bay=None, orientation=None)], ttl=TTL, now=NOW)
    data = json.loads(read_facts(conn, "A", "floorplan", now=NOW)[0].value)
    assert data["bay"] is None and data["orientation"] is None and data["structure"] == "판상형"


def test_load_keeps_multiple_sources() -> None:
    conn = _conn()
    load_seed(conn, [_rec("https://x/1"), _rec("https://y/2")], ttl=TTL, now=NOW)
    assert len(read_facts(conn, "A", "floorplan", now=NOW)) == 2


def test_load_idempotent_resume() -> None:
    conn = _conn()
    load_seed(conn, [_rec("https://x/1")], ttl=TTL, now=NOW)
    again = load_seed(conn, [_rec("https://x/1")], ttl=TTL, now=NOW)
    assert again["skipped"] == 1
