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
    BadRequestError,
    KakaoLocalClient,
    PoiResult,
    QuotaExceeded,
    TransientError,
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


# ── 오류 3분류: 일시적(timeout/5xx) 재시도→TransientError · 영구(4xx) raise (C56) ──
def _client_kw(handler, **kw) -> KakaoLocalClient:  # type: ignore[no-untyped-def]
    tr = httpx.MockTransport(handler)
    return KakaoLocalClient(
        api_key="k", client=httpx.Client(transport=tr), sleep=lambda _s: None, **kw
    )


def test_client_connect_timeout_retries_then_transient() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectTimeout("handshake timed out")  # 라이브 크래시 재현

    with pytest.raises(TransientError):  # 크래시(미처리 httpx 예외) 아님
        _client_kw(handler, max_retries=2).search("SW8", None, x=127.1, y=37.5)
    assert calls["n"] == 3  # 1 + 2 재시도


def test_client_5xx_retries_then_transient() -> None:
    c = _client_kw(lambda req: httpx.Response(503, json={}), max_retries=1)
    with pytest.raises(TransientError):
        c.search("SW8", None, x=127.1, y=37.5)


def test_client_transient_then_success_recovers() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("blip")
        return httpx.Response(200, json={
            "documents": [{"distance": "100", "place_name": "x"}], "meta": {"total_count": 1},
        })

    r = _client_kw(handler, max_retries=2).search("SW8", None, x=127.1, y=37.5)
    assert r.nearest_name == "x" and calls["n"] == 2  # 재시도 후 성공


def test_client_permanent_4xx_raises_not_transient() -> None:
    # 401(키 문제 등) = 체계적 영구 → 즉시 raise(재시도 없음·TransientError/QuotaExceeded 아님)
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={})

    with pytest.raises(httpx.HTTPStatusError):
        _client_kw(handler, max_retries=2).search("SW8", None, x=127.1, y=37.5)
    assert calls["n"] == 1  # 영구 → 재시도 안 함


def test_client_403_still_aborts() -> None:
    # 403(forbidden = 체계적) → 여전히 abort(httpx.HTTPStatusError) — BadRequest/Quota 아님
    c = _client_kw(lambda req: httpx.Response(403, json={}), max_retries=2)
    with pytest.raises(httpx.HTTPStatusError):
        c.search("SW8", None, x=127.1, y=37.5)


def test_client_400_quota_code_minus10_raises_quota_exceeded() -> None:
    # ★ C61 크래시 진짜 원인: Kakao는 일쿼터 초과를 HTTP 400 + code -10로 신호(429 아님).
    # → QuotaExceeded(우아 중단·자가치유) · 재시도 안 함 · BadRequest/HTTPStatusError 아님.
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={
            "errorType": "BadRequest", "code": -10,
            "message": "API limit has been exceeded.",
        })

    with pytest.raises(QuotaExceeded):
        _client_kw(handler, max_retries=2).search("SW8", None, x=127.1, y=37.5)
    assert calls["n"] == 1  # quota → 재시도 안 함


def test_client_400_quota_detected_by_message_fallback() -> None:
    # code 필드 없어도 메시지 substring으로 quota 판별(body 형태 변형 방어).
    c = _client_kw(lambda req: httpx.Response(
        400, json={"msg": "API limit has been exceeded."}), max_retries=2)
    with pytest.raises(QuotaExceeded):
        c.search("SW8", None, x=127.1, y=37.5)


def test_client_400_genuine_bad_request_raises_bad_request() -> None:
    # 진짜 per-row 400(quota -10 아님) → BadRequestError(러너가 skip+continue) · 재시도 없음.
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"code": -1, "message": "bad coordinate"})

    with pytest.raises(BadRequestError):
        _client_kw(handler, max_retries=2).search("SW8", None, x=127.1, y=37.5)
    assert calls["n"] == 1  # 400은 재요청해도 안 변함 → 재시도 없음


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


# ── 러너 transient skip-continue (C56 — crash 0, resume-correct) ──
class FlakyClient:
    """transient_on 콜 번호에서 TransientError 1회(재시도 소진 가정), 그 외 정상."""

    def __init__(self, transient_on: int) -> None:
        self.calls = 0
        self.transient_on = transient_on

    def search(self, category: str, keyword: str | None, *, x: float, y: float) -> PoiResult:
        self.calls += 1
        if self.calls == self.transient_on:
            raise TransientError("blip")
        return PoiResult(100 + self.calls, f"{category}-poi", 2, 5)


def test_runner_transient_skips_complex_and_continues(db: sqlite3.Connection) -> None:
    # C1 첫 카테고리에서 transient → C1 skip(미적재·다음 run retry), C2 정상. **크래시 0**.
    client = FlakyClient(transient_on=1)
    r = enrich_poi(db, client, now=NOW, limit=10)  # type: ignore[arg-type]
    assert r["quota_hit"] is False
    assert r["transient_skips"] == 1
    assert r["complexes"] == 1  # C2만 완료(C1 skip)
    assert done_categories(db, "C1") == set()  # 첫 cat 실패 → 미적재(영구 갭 0, retry 대상)
    assert done_categories(db, "C2") == {c for c, _ in CATEGORIES}  # 타 단지 정상


def test_runner_transient_midcomplex_preserves_partial(db: sqlite3.Connection) -> None:
    # C1 3번째 카테고리에서 transient → 앞 2개 보존(다음 run이 나머지 retry), C2 정상.
    client = FlakyClient(transient_on=3)
    r = enrich_poi(db, client, now=NOW, limit=10)  # type: ignore[arg-type]
    assert r["transient_skips"] == 1
    assert 0 < len(done_categories(db, "C1")) < len(CATEGORIES)  # 부분 보존(resume)
    assert done_categories(db, "C2") == {c for c, _ in CATEGORIES}


def test_runner_transient_then_resume_completes(db: sqlite3.Connection) -> None:
    # 1차 transient skip → 2차(정상 클라)가 미완 단지/카테고리 완주(영구 갭 0).
    enrich_poi(db, FlakyClient(transient_on=1), now=NOW, limit=10)  # type: ignore[arg-type]
    r2 = enrich_poi(db, FakeClient(), now=NOW, limit=10)  # type: ignore[arg-type]
    assert r2["transient_skips"] == 0
    assert done_categories(db, "C1") == {c for c, _ in CATEGORIES}  # 다음 run에서 완주


# ── 러너 per-row 400 skip-continue (poi-fix — quota 아닌 진짜 bad-request) ──
class BadReqClient:
    """bad_on 콜 번호에서 BadRequestError 1회(진짜 per-row 400), 그 외 정상."""

    def __init__(self, bad_on: int) -> None:
        self.calls = 0
        self.bad_on = bad_on

    def search(self, category: str, keyword: str | None, *, x: float, y: float) -> PoiResult:
        self.calls += 1
        if self.calls == self.bad_on:
            raise BadRequestError("per-row 400")
        return PoiResult(100 + self.calls, f"{category}-poi", 2, 5)


def test_runner_bad_request_skips_category_and_continues(db: sqlite3.Connection) -> None:
    # C1 첫 카테고리에서 per-row 400 → 그 카테고리만 skip 마킹, **같은 단지 나머지는 계속**.
    # transient(단지 전체 skip)와 다름. crash 0·tick 완주·C2 정상.
    client = BadReqClient(bad_on=1)
    r = enrich_poi(db, client, now=NOW, limit=10)  # type: ignore[arg-type]
    assert r["quota_hit"] is False
    assert r["bad_request_skips"] == 1
    assert r["complexes"] == 2  # C1·C2 둘 다 처리(C1은 5 적재 + 1 skip-마킹)
    # 첫 카테고리(SW8)는 skip 마킹 → done에 잡힘(재호출 방지) · 나머지 5개 정상 적재.
    assert done_categories(db, "C1") == {c for c, _ in CATEGORIES}
    assert done_categories(db, "C2") == {c for c, _ in CATEGORIES}


def test_runner_bad_request_marks_attempted_no_reretry(db: sqlite3.Connection) -> None:
    # 1차 per-row 400 skip → 2차 run은 그 카테고리 **재호출 안 함**(무한루프 0).
    enrich_poi(db, BadReqClient(bad_on=1), now=NOW, limit=10)  # type: ignore[arg-type]
    client2 = BadReqClient(bad_on=999)  # 호출되면 정상 반환(재호출 여부 확인용)
    r2 = enrich_poi(db, client2, now=NOW, limit=10)  # type: ignore[arg-type]
    assert r2["complexes"] == 0 and client2.calls == 0  # 전부 done(+skip) → 재호출 0


def test_runner_bad_request_skip_invisible_to_read_poi(db: sqlite3.Connection) -> None:
    # missing=KEEP: skip 마커는 read_poi(카드/검색 attach)에 안 보임 → 5개 카테고리만.
    enrich_poi(db, BadReqClient(bad_on=1), now=NOW, limit=10)  # type: ignore[arg-type]
    got = read_poi(db, ["C1"])
    cats = {p.category for p in got["C1"]}
    assert all(":skip" not in c for c in cats)  # 마커 비노출
    assert len(got["C1"]) == len(CATEGORIES) - 1  # SW8 skip → 5개만


def test_runner_quota_400_graceful_stop_not_skip_storm(db: sqlite3.Connection) -> None:
    # ★ 진짜 원인 회귀가드: quota(400 code -10)는 QuotaExceeded로 와 **우아 중단** —
    # per-row skip 폭주(수만 마킹)로 백필 오염시키지 않음. quota_hit=True·skip 0.
    client = FakeClient(quota_after=3)  # 3콜 후 QuotaExceeded(quota-400 동형)
    r = enrich_poi(db, client, now=NOW, limit=10)  # type: ignore[arg-type]
    assert r["quota_hit"] is True
    assert r["bad_request_skips"] == 0  # quota는 skip 마킹 안 함


# ── store ──
def test_write_upsert_and_read(db: sqlite3.Connection) -> None:
    write_poi(db, "C1", "SW8", PoiResult(467, "둔촌동역", 1, 3), now=NOW)
    write_poi(db, "C1", "SW8", PoiResult(400, "둔촌동역2", 1, 4), now=NOW)  # upsert
    got = read_poi(db, ["C1", "C2"])
    assert len(got["C1"]) == 1 and got["C1"][0].nearest_dist_m == 400  # 덮어씀
    assert got["C1"][0].label == "지하철역"
    assert got["C2"] == []  # 미적재 → computed-or-dash 빈 리스트
