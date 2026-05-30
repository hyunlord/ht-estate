"""POST /complexes/search — 라우트 스모크 (TestClient + DB 오버라이드)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app, get_db


@pytest.fixture
def client(search_db: sqlite3.Connection) -> Iterator[TestClient]:
    def _override() -> Iterator[sqlite3.Connection]:
        yield search_db

    app.dependency_overrides[get_db] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_search_route_returns_candidates(client: TestClient) -> None:
    resp = client.post("/complexes/search", json={"parking_ratio_gte": 1.3})
    assert resp.status_code == 200
    ids = {c["complex_id"] for c in resp.json()}
    assert ids == {"C1", "C3"}


def test_search_route_empty_spec_returns_all(client: TestClient) -> None:
    resp = client.post("/complexes/search", json={})
    assert resp.status_code == 200
    assert {c["complex_id"] for c in resp.json()} == {"C1", "C2", "C3", "C4"}


def test_search_route_includes_representative_trade(client: TestClient) -> None:
    resp = client.post("/complexes/search", json={"price_min": 150000})
    body = resp.json()
    assert len(body) == 1
    c2 = body[0]
    assert c2["complex_id"] == "C2"
    assert c2["representative_trade"]["match_confidence"] == 0.7  # 추정매칭 배지


def test_search_route_rejects_incoherent_spec(client: TestClient) -> None:
    resp = client.post(
        "/complexes/search", json={"approval_year_min": 2020, "approval_year_max": 2010}
    )
    assert resp.status_code == 422  # Pydantic 검증 실패


def test_search_route_rejects_gym_field(client: TestClient) -> None:
    # R1: gym 필드는 모델에 없음 → 보내도 무시(extra ignore)되며 필터에 안 쓰임
    resp = client.post("/complexes/search", json={"has_gym": True})
    assert resp.status_code == 200
    assert {c["complex_id"] for c in resp.json()} == {"C1", "C2", "C3", "C4"}  # gym 무관 전체
