"""property_type (P5-1) — 비-아파트 커버리지 토대. 스키마 백필·검색 필터·criterion·NL 자동커버.

이 PR(P5-1a)은 property_type 머신만(키리스). 실 비-아파트 적재(MOLIT RH/Offi/SH 클라이언트·
건물 도출·geocode·cron)는 P5-1b(라이브). markers 엔드포인트는 #47(feat/map-first) 소관이나
동일 `_complex_where`를 쓰므로 머지 시 property_type 필터를 자동 상속.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app, get_db
from app.search.criteria import REGISTRY
from app.search.nl_parse import parse_query, registry_catalog
from app.store.db import get_connection, init_db


@pytest.fixture
def client(search_db: sqlite3.Connection) -> Iterator[TestClient]:
    # 비-아파트 표본 1건 추가(officetel) — 좌표 보유. 기존 시드 4건은 init_db가 apartment 백필.
    search_db.execute(
        "INSERT INTO complex (complex_id, name, property_type, lat, lng, source_url) "
        "VALUES ('OFFI1', '샘플오피스텔', 'officetel', 37.5, 127.05, 'https://k-apt/OFFI1')"
    )
    search_db.commit()

    def _override() -> Iterator[sqlite3.Connection]:
        yield search_db

    app.dependency_overrides[get_db] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()


def _ids(resp: object) -> set[str]:
    return {c["complex_id"] for c in resp}  # type: ignore[union-attr]


# ───────────────────────── 스키마 백필 ─────────────────────────


def test_migration_backfills_null_to_apartment() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    # 컬럼 추가 후 property_type NULL 행을 직접 삽입 → init_db 재호출이 apartment 백필(멱등).
    conn.execute("INSERT INTO complex (complex_id, name) VALUES ('X', '미지정')")
    conn.commit()
    init_db(conn)
    row = conn.execute("SELECT property_type FROM complex WHERE complex_id='X'").fetchone()
    assert row[0] == "apartment"


# ───────────────────────── 검색 필터 ─────────────────────────


def test_search_no_filter_includes_all_types(client: TestClient) -> None:
    resp = client.post("/complexes/search", json={})
    assert _ids(resp.json()) == {"C1", "C2", "C3", "C4", "OFFI1"}  # 아파트 + 비-아파트


def test_search_property_type_apartment(client: TestClient) -> None:
    resp = client.post("/complexes/search", json={"property_type": "apartment"})
    assert _ids(resp.json()) == {"C1", "C2", "C3", "C4"}  # 비-아파트 제외


def test_search_property_type_officetel(client: TestClient) -> None:
    resp = client.post("/complexes/search", json={"property_type": "officetel"})
    assert _ids(resp.json()) == {"OFFI1"}  # 비-아파트만


def test_search_rejects_unknown_property_type(client: TestClient) -> None:
    resp = client.post("/complexes/search", json={"property_type": "villa"})
    assert resp.status_code == 422  # Literal 밖 → Pydantic 거부


# ───────────────────────── criterion + NL 자동커버 ─────────────────────────


def test_property_type_criterion_registered() -> None:
    crit = REGISTRY["property_type"]
    assert crit.hard_able and not crit.soft_able
    assert crit.hard_fields == ("property_type",)
    assert set(crit.values) == {"apartment", "rowhouse", "officetel", "detached"}


def test_registry_catalog_exposes_property_type_values() -> None:
    cat = registry_catalog()
    assert "`property_type`" in cat
    assert "officetel" in cat and "rowhouse" in cat  # enum 노출 → 라이브 LLM "오피스텔/빌라" 매핑


def test_nl_auto_covers_property_type() -> None:
    # mock runner: "오피스텔" 질의 → property_type=officetel. criterion 등록되어 grounding 통과.
    def runner(prompt: str, max_turns: int) -> str:
        return json.dumps({"hard": {"property_type": "officetel"}, "soft": {}}, ensure_ascii=False)

    parsed = parse_query("오피스텔만", runner=runner)
    assert parsed.spec.property_type == "officetel"


def test_nl_drops_invalid_property_type_value() -> None:
    # 환각/비-enum 값은 spec 검증에서 거부 → 파싱 실패(grounded). 발명 차단.
    from app.search.nl_parse import QueryParseError

    def runner(prompt: str, max_turns: int) -> str:
        return json.dumps({"hard": {"property_type": "빌라"}}, ensure_ascii=False)

    with pytest.raises(QueryParseError):
        parse_query("빌라", runner=runner)
