"""POI 근접 (poi-1) — compute·Kakao 클라(MockTransport)·러너(resume·quota)·store. 키리스.

실 HTTP 0(MockTransport/FakeClient 주입). 좌표 read·poi write만 → 지문/counts 불변.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import httpx
import pytest

from app.poi.proximity import (
    CATEGORIES,
    KakaoLocalClient,
    PoiResult,
    QuotaExceeded,
    compute,
)
from app.poi.runner import enrich_poi
from app.poi.store import done_categories, read_poi, write_poi
from app.store.db import get_connection, init_db

NOW = datetime(2026, 6, 9, tzinfo=UTC)


# ── compute ──
def test_compute_nearest_and_counts() -> None:
    docs = [
        {"distance": "169", "place_name": "CU 포레온"},
        {"distance": "480", "place_name": "GS25"},
        {"distance": "700", "place_name": "세븐일레븐"},
    ]
    r = compute(docs, total_count=46)
    assert r.nearest_dist_m == 169 and r.nearest_name == "CU 포레온"
    assert r.count_500m == 2  # ≤500: 169,480
    assert r.count_1km == 46  # meta.total_count


def test_compute_empty() -> None:
    r = compute([], total_count=0)
    assert r.nearest_dist_m is None and r.nearest_name is None
    assert r.count_500m is None and r.count_1km == 0


# ── KakaoLocalClient (MockTransport) ──
def _client(handler) -> KakaoLocalClient:  # type: ignore[no-untyped-def]
    tr = httpx.MockTransport(handler)
    return KakaoLocalClient(api_key="k", client=httpx.Client(transport=tr))


def test_client_category_path() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path.endswith("/category.json")
        assert req.url.params["category_group_code"] == "SW8"
        return httpx.Response(200, json={
            "documents": [{"distance": "467", "place_name": "둔촌동역"}],
            "meta": {"total_count": 3},
        })

    r = _client(handler).search("SW8", None, x=127.1, y=37.5)
    assert r.nearest_name == "둔촌동역" and r.count_1km == 3


def test_client_keyword_path_for_park() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path.endswith("/keyword.json")
        assert req.url.params["query"] == "공원"
        return httpx.Response(200, json={
            "documents": [{"distance": "261", "place_name": "제2호 근린공원"}],
            "meta": {"total_count": 27},
        })

    r = _client(handler).search("PARK", "공원", x=127.1, y=37.5)
    assert r.nearest_dist_m == 261 and r.count_1km == 27


def test_client_429_raises_quota_exceeded() -> None:
    c = _client(lambda req: httpx.Response(429, json={}))
    with pytest.raises(QuotaExceeded):
        c.search("SW8", None, x=127.1, y=37.5)


# ── 러너 (resume·quota-graceful) ──
class FakeClient:
    """결정론 mock — 카테고리별 고정 결과. quota_after 콜 수 후 QuotaExceeded."""

    def __init__(self, quota_after: int | None = None) -> None:
        self.calls = 0
        self.quota_after = quota_after

    def search(self, category: str, keyword: str | None, *, x: float, y: float) -> PoiResult:
        self.calls += 1
        if self.quota_after is not None and self.calls > self.quota_after:
            raise QuotaExceeded("429")
        return PoiResult(nearest_dist_m=100 + self.calls, nearest_name=f"{category}-poi",
                         count_500m=2, count_1km=5)


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.executemany(
        "INSERT INTO complex (complex_id, name, property_type, lat, lng) VALUES (?,?,?,?,?)",
        [("C1", "가", "apartment", 37.5, 127.1), ("C2", "나", "officetel", 37.6, 127.0),
         ("C3", "다", "rowhouse", None, None)],  # C3 무좌표 → POI 대상 아님
    )
    conn.commit()
    return conn


def test_runner_writes_all_categories_for_geocoded(db: sqlite3.Connection) -> None:
    client = FakeClient()
    r = enrich_poi(db, client, now=NOW, limit=10)  # type: ignore[arg-type]
    assert r["quota_hit"] is False
    assert r["complexes"] == 2  # C1,C2 (C3 무좌표 제외)
    assert r["calls"] == 2 * len(CATEGORIES)
    assert done_categories(db, "C1") == {c for c, _ in CATEGORIES}
    assert done_categories(db, "C3") == set()  # 무좌표 → 미적재


def test_runner_resume_skips_done(db: sqlite3.Connection) -> None:
    enrich_poi(db, FakeClient(), now=NOW, limit=10)  # type: ignore[arg-type]
    client2 = FakeClient()
    r = enrich_poi(db, client2, now=NOW, limit=10)  # type: ignore[arg-type]
    assert r["complexes"] == 0 and client2.calls == 0  # 전부 done → skip


def test_runner_quota_graceful_partial(db: sqlite3.Connection) -> None:
    # 8콜 후 429 → 쓴 만큼 보존·crash 없음
    client = FakeClient(quota_after=8)
    r = enrich_poi(db, client, now=NOW, limit=10)  # type: ignore[arg-type]
    assert r["quota_hit"] is True
    # C1 완료(6), C2는 2개만 쓰고 중단 → 다음 run이 이어받음
    assert done_categories(db, "C1") == {c for c, _ in CATEGORIES}
    assert 0 < len(done_categories(db, "C2")) < len(CATEGORIES)


# ── store ──
def test_write_upsert_and_read(db: sqlite3.Connection) -> None:
    write_poi(db, "C1", "SW8", PoiResult(467, "둔촌동역", 1, 3), now=NOW)
    write_poi(db, "C1", "SW8", PoiResult(400, "둔촌동역2", 1, 4), now=NOW)  # upsert
    got = read_poi(db, ["C1", "C2"])
    assert len(got["C1"]) == 1 and got["C1"][0].nearest_dist_m == 400  # 덮어씀
    assert got["C1"][0].label == "지하철역"
    assert got["C2"] == []  # 미적재 → computed-or-dash 빈 리스트
