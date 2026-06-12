"""POST /complexes/markers — 마커 피드(P4-3a-2). bbox 내 전체 단지 최소필드·필터 존중·고캡·경량."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app, get_db
from app.search.repo import search_markers
from app.search.spec import HardFilterSpec

# 강남 시드(C1·C2·C3 좌표 보유, C4 좌표 없음)를 모두 덮는 bbox.
ALL_BBOX = {"min_lat": 37.4, "max_lat": 37.6, "min_lng": 127.0, "max_lng": 127.1}


@pytest.fixture
def client(search_db: sqlite3.Connection) -> Iterator[TestClient]:
    def _override() -> Iterator[sqlite3.Connection]:
        yield search_db

    app.dependency_overrides[get_db] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_markers_returns_viewport_complexes_minimal(client: TestClient) -> None:
    resp = client.post("/complexes/markers", json={**ALL_BBOX})
    assert resp.status_code == 200
    body = resp.json()
    # 소량(3단지·≤MAX) → mode='markers'(개별). C4 좌표 없음 → 제외.
    assert body["mode"] == "markers"
    markers = body["markers"]
    assert {m["complex_id"] for m in markers} == {"C1", "C2", "C3"}
    m = markers[0]
    # 최소 필드만 — criteria_eval/랭킹/enrichment 없음(경량). marker-zoom-rent ②: 월세 라벨용
    # deposit/monthly_rent/rent_type 추가(매매면 None).
    assert set(m.keys()) == {
        "complex_id", "name", "lat", "lng", "price", "net_area",
        "deposit", "monthly_rent", "rent_type",
    }
    assert m["lat"] is not None and m["lng"] is not None


def test_markers_have_representative_price(client: TestClient) -> None:
    resp = client.post("/complexes/markers", json={**ALL_BBOX})
    by_id = {m["complex_id"]: m for m in resp.json()["markers"]}
    assert by_id["C1"]["price"] == 142000  # 최근 거래(2025-04-15)
    assert by_id["C1"]["net_area"] == 84.97


def test_markers_respect_hard_filter(client: TestClient) -> None:
    # parking_ratio_gte 1.3 → C1(1.5)·C3(1.8)만(C2 0.8 제외) — search와 동일 hard 필터.
    resp = client.post("/complexes/markers", json={**ALL_BBOX, "parking_ratio_gte": 1.3})
    assert {m["complex_id"] for m in resp.json()["markers"]} == {"C1", "C3"}


def test_markers_respect_bbox(client: TestClient) -> None:
    # C1만 덮는 좁은 bbox.
    resp = client.post(
        "/complexes/markers",
        json={"min_lat": 37.499, "max_lat": 37.501, "min_lng": 127.039, "max_lng": 127.041},
    )
    assert {m["complex_id"] for m in resp.json()["markers"]} == {"C1"}


def test_search_markers_cap(search_db: sqlite3.Connection) -> None:
    # 고캡 동작 — cap으로 마커 수 제한(저줌 폭주 흡수).
    spec = HardFilterSpec.model_validate({**ALL_BBOX})
    assert len(search_markers(search_db, spec, cap=2)) == 2
    assert len(search_markers(search_db, spec, cap=100)) == 3  # 좌표 보유 3단지
