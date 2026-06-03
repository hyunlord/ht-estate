"""POST /complexes/search/nl — NL 경로 라우트 스모크 (TestClient + DB·runner 오버라이드).

claude -p 러너를 mock으로 주입(키리스). NL→spec→hard 필터+랭킹 연결 · 감지/unsupported 표면화 ·
demote-not-exclude(soft만이면 SET 불변) · 파싱 실패 422 · 수동 spec 경로 회귀 0.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app, get_db, get_query_runner


def _client(search_db: sqlite3.Connection, payload: object) -> TestClient:
    """search_db + payload를 JSON으로 돌려주는 mock 러너를 주입한 TestClient."""

    def _override_db() -> Iterator[sqlite3.Connection]:
        yield search_db

    def _mock_runner() -> Callable[[str, int], str]:
        def run(prompt: str, max_turns: int) -> str:
            return json.dumps(payload, ensure_ascii=False)

        return run

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_query_runner] = _mock_runner
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def test_nl_route_hard_filter(search_db: sqlite3.Connection) -> None:
    """NL→hard spec→필터 — 수동 경로와 동일 SET(parking_ratio_gte 1.3 → C1·C3)."""
    client = _client(search_db, {"hard": {"parking_ratio_gte": 1.3}, "soft": {}})
    resp = client.post("/complexes/search/nl", json={"query": "주차 넉넉한 곳만"})
    assert resp.status_code == 200
    body = resp.json()
    assert {c["complex_id"] for c in body["candidates"]} == {"C1", "C3"}
    keys = {(d["criterion_key"], d["mode"]) for d in body["detected"]}
    assert ("parking_ratio", "hard") in keys


def test_nl_route_soft_demote_not_exclude(search_db: sqlite3.Connection) -> None:
    """soft만(하드 없음)이면 후보 SET 불변 — 전체 4단지 유지(demote-not-exclude)."""
    client = _client(
        search_db, {"hard": {}, "soft": {"pet": "preferred"}}
    )
    resp = client.post("/complexes/search/nl", json={"query": "강아지 되면 좋고"})
    assert resp.status_code == 200
    body = resp.json()
    assert {c["complex_id"] for c in body["candidates"]} == {"C1", "C2", "C3", "C4"}
    assert ("pet", "soft") in {(d["criterion_key"], d["mode"]) for d in body["detected"]}


def test_nl_route_unsupported_surfaced(search_db: sqlite3.Connection) -> None:
    client = _client(search_db, {"hard": {}, "soft": {}, "unsupported": ["바다 전망"]})
    resp = client.post("/complexes/search/nl", json={"query": "바다 전망 좋은 곳"})
    assert resp.status_code == 200
    assert resp.json()["unsupported"] == ["바다 전망"]


def test_nl_route_parse_failure_422(search_db: sqlite3.Connection) -> None:
    """빈/비-JSON LLM 출력 → 파싱 실패 422(서버 500 아님)."""
    client = _client(search_db, None)  # json.dumps(None) = "null" → JSON 객체 아님
    resp = client.post("/complexes/search/nl", json={"query": "..."})
    assert resp.status_code == 422


def test_nl_route_returns_resolved_spec(search_db: sqlite3.Connection) -> None:
    """투명성 — 확정 spec을 응답에 실어 #3가 감지·반영을 표시할 수 있게."""
    client = _client(
        search_db, {"hard": {"has_daycare": True}, "soft": {"criteria": [{"key": "subway_time"}]}}
    )
    resp = client.post("/complexes/search/nl", json={"query": "어린이집 있고 역세권이면"})
    body = resp.json()
    assert body["spec"]["has_daycare"] is True
    assert any(c["key"] == "subway_time" for c in body["spec"]["soft"]["criteria"])


def test_manual_spec_path_unchanged(search_db: sqlite3.Connection) -> None:
    """회귀 0 — 수동 spec 경로(/complexes/search)는 NL 배선 후에도 그대로."""

    def _override_db() -> Iterator[sqlite3.Connection]:
        yield search_db

    app.dependency_overrides[get_db] = _override_db
    client = TestClient(app)
    resp = client.post("/complexes/search", json={"parking_ratio_gte": 1.3})
    assert resp.status_code == 200
    assert {c["complex_id"] for c in resp.json()} == {"C1", "C3"}
