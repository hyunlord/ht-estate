"""geocode 지문 헬퍼 — 결정론·아파트한정·좌표변경 감지. (enrich-1b · 키리스)"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from app.store.db import get_connection, init_db

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from geocode_fingerprint import geocode_fingerprint  # noqa: E402


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.executemany(
        "INSERT INTO complex (complex_id, property_type, lat, lng) VALUES (?, ?, ?, ?)",
        [
            ("A2", "apartment", 37.6, 127.1),
            ("A1", "apartment", 37.5, 127.0),
            ("R1", "rowhouse", 37.7, 127.2),  # 비-아파트 — 지문에서 제외
        ],
    )
    conn.commit()
    return conn


def test_deterministic_and_apartment_only(db: sqlite3.Connection) -> None:
    fp1, n1 = geocode_fingerprint(db)
    fp2, n2 = geocode_fingerprint(db)
    assert fp1 == fp2  # 결정론
    assert n1 == 2  # 아파트만(rowhouse 제외)
    assert len(fp1) == 16


def test_changing_coord_changes_fingerprint(db: sqlite3.Connection) -> None:
    fp_before, _ = geocode_fingerprint(db)
    db.execute("UPDATE complex SET lat = 38.0 WHERE complex_id = 'A1'")
    fp_after, _ = geocode_fingerprint(db)
    assert fp_before != fp_after  # 좌표 변경 → 지문 변경(감지)


def test_nonapt_coord_does_not_affect(db: sqlite3.Connection) -> None:
    fp_before, _ = geocode_fingerprint(db)
    db.execute("UPDATE complex SET lat = 99.0 WHERE complex_id = 'R1'")  # 비-아파트 변경
    fp_after, _ = geocode_fingerprint(db)
    assert fp_before == fp_after  # 아파트 한정이라 비-아파트 변경엔 불변
