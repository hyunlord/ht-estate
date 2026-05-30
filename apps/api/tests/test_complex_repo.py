"""complex 적재 — 실캡처 ComplexInfo → upsert(provenance·파생) + 멱등."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.sources.kapt import parse_complex_info
from app.store.complex_repo import upsert_complex
from app.store.db import get_connection, init_db

FixtureLoader = Callable[[str], str]
FIXED_NOW = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)


def _info(load_fixture: FixtureLoader):  # type: ignore[no-untyped-def]
    info = parse_complex_info(load_fixture("kapt_basis.json"), load_fixture("kapt_detail.json"))
    assert info is not None
    return info


def test_upsert_writes_row_with_provenance_and_derived(load_fixture: FixtureLoader) -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    upsert_complex(conn, _info(load_fixture), updated_at=FIXED_NOW)

    row = conn.execute("SELECT * FROM complex WHERE complex_id = 'A10027474'").fetchone()
    assert row is not None
    assert row["name"] == "역삼자이아파트"
    assert row["bjd_code"] == "1168010100"  # 조인 narrowing 키
    assert row["household_count"] == 408
    assert row["parking_total"] == 615
    assert round(row["parking_ratio"], 4) == 1.5074  # 파생
    assert row["has_gym"] == 0  # 실 amenities에 헬스장 없음
    assert row["amenities_raw"] is not None and "관리사무소" in row["amenities_raw"]
    assert row["approval_date"] == "2016-06-22"
    # provenance
    assert row["updated_at"] == FIXED_NOW.isoformat()
    assert row["source_url"] == "https://www.k-apt.go.kr/kaptinfo/kaptinfobasis.do?kaptCode=A10027474"
    assert "serviceKey" not in row["source_url"]  # 시크릿 미포함


def test_upsert_is_idempotent_and_updates(load_fixture: FixtureLoader) -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    info = _info(load_fixture)
    upsert_complex(conn, info, updated_at=FIXED_NOW)
    # 두 번째 적재 — 갱신된 updated_at으로, 행은 여전히 1개
    later = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    upsert_complex(conn, info, updated_at=later)

    rows = conn.execute("SELECT COUNT(*) AS c FROM complex").fetchone()
    assert rows["c"] == 1
    row = conn.execute("SELECT updated_at FROM complex WHERE complex_id='A10027474'").fetchone()
    assert row["updated_at"] == later.isoformat()  # 갱신됨
