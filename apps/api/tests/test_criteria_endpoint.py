"""GET /criteria (frontend-polish-1) — REGISTRY/퀵필터 카탈로그 직렬화. read-only·키리스.

인메모리 직렬화(DB 무접촉) → 지문/counts 불변. 신규 criteria(학교거리·POI 풀세트) 포함·shape.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.store.db import get_connection, init_db


@pytest.fixture
def client() -> Iterator[TestClient]:
    from app.main import app, get_db

    conn = get_connection(":memory:")
    init_db(conn)
    app.dependency_overrides[get_db] = lambda: conn
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_criteria_shape(client: TestClient) -> None:
    r = client.get("/criteria")
    assert r.status_code == 200
    body = r.json()
    assert "criteria" in body and "quick_filters" in body
    crit = {c["key"]: c for c in body["criteria"]}
    # 신규 criteria 포함
    for k in ("elem_dist", "mid_dist", "high_dist", "mart", "conv", "hospital", "pharmacy", "park"):
        assert k in crit, k
    # shape — 학교거리(numeric·lower_better·soft+hard)
    e = crit["elem_dist"]
    assert e["label"] == "초등학교 거리" and e["value_type"] == "numeric"
    assert e["direction"] == "lower_better" and e["soft_able"] and e["hard_able"]
    assert e["hard_fields"] == ["elem_max_dist_m"]
    # POI count(higher_better)
    assert crit["conv"]["direction"] == "higher_better"


def test_quick_filters_shape(client: TestClient) -> None:
    body = client.get("/criteria").json()
    qf = {q["id"]: q for q in body["quick_filters"]}
    # 신규 퀵 토글 등장(학교·POI)
    for qid in ("elem_school", "conv_poi", "hospital_poi", "park_poi"):
        assert qid in qf, qid
    # hard 토글 배선(field+value)
    assert qf["elem_school"]["apply"] == "hard"
    assert qf["elem_school"]["hard_field"] == "elem_max_dist_m"
    assert qf["elem_school"]["hard_value"] == 500
    # soft 토글 배선(soft_key)
    assert qf["has_daycare"]["apply"] == "soft" and qf["has_daycare"]["soft_key"] == "has_daycare"
    # 기존 토글 id 보존(e2e 회귀 0)
    assert "subway_poi" in qf and "mart_poi" in qf


def test_criteria_readonly_no_db_touch(client: TestClient) -> None:
    # 두 번 호출해도 결정론·DB write 0(카탈로그는 인메모리 직렬화)
    a = client.get("/criteria").json()
    b = client.get("/criteria").json()
    assert a == b
