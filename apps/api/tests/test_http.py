"""_http 재시도·페이지네이션 단위 테스트 — backoff=0으로 즉시(슬립 없이)."""

from __future__ import annotations

from xml.etree.ElementTree import fromstring

import httpx
import pytest

from app.sources._http import ensure_success, fetch_text, paginate, resolve_total_count
from app.sources.errors import PublicDataError

OK_BODY = "<response><header><resultCode>00</resultCode></header></response>"


def _client(handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_text_retries_5xx_then_succeeds() -> None:
    state = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] < 3:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, text=OK_BODY)

    out = fetch_text("http://x", {}, client=_client(handler), retries=3, backoff=0.0)
    assert out == OK_BODY
    assert state["n"] == 3


def test_fetch_text_retries_transport_error_then_succeeds() -> None:
    state = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] < 2:
            raise httpx.ConnectError("boom", request=req)
        return httpx.Response(200, text=OK_BODY)

    out = fetch_text("http://x", {}, client=_client(handler), retries=3, backoff=0.0)
    assert out == OK_BODY
    assert state["n"] == 2


def test_fetch_text_exhausts_retries_and_raises() -> None:
    state = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(500, text="err")

    with pytest.raises(httpx.HTTPStatusError):
        fetch_text("http://x", {}, client=_client(handler), retries=2, backoff=0.0)
    assert state["n"] == 2  # 정확히 retries회만 시도


def test_fetch_text_4xx_immediate_no_retry() -> None:
    state = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(404, text="not found")

    with pytest.raises(httpx.HTTPStatusError):
        fetch_text("http://x", {}, client=_client(handler), retries=3, backoff=0.0)
    assert state["n"] == 1


# ───────── C22 회복력 (일시 오류 재시도 / 영구 빠른실패 / 백오프) ─────────


def test_fetch_text_retries_429_then_succeeds() -> None:
    # 429(레이트리밋)는 영구 4xx와 달리 재시도한다.
    state = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] < 3:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, text=OK_BODY)

    out = fetch_text("http://x", {}, client=_client(handler), backoff=0.0, sleep=lambda _d: None)
    assert out == OK_BODY
    assert state["n"] == 3


def test_fetch_text_429_honors_retry_after() -> None:
    # Retry-After(초)를 백오프 대신 존중한다.
    slept: list[float] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if not slept:  # 첫 호출만 429
            return httpx.Response(429, headers={"Retry-After": "7"}, text="wait")
        return httpx.Response(200, text=OK_BODY)

    out = fetch_text("http://x", {}, client=_client(handler), sleep=slept.append)
    assert out == OK_BODY
    assert slept == [7.0]  # 백오프(0.5…)가 아니라 Retry-After 7초


def test_fetch_text_403_permanent_fast_fail() -> None:
    # 403(인가 오류)은 영구 → 재시도 없이 즉시 실패(일일캡·키 오류 마스킹 금지).
    state = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(403, text="forbidden")

    with pytest.raises(httpx.HTTPStatusError):
        fetch_text("http://x", {}, client=_client(handler), sleep=lambda _d: None)
    assert state["n"] == 1  # 단 한 번


def test_fetch_text_backoff_is_capped_and_jittered() -> None:
    # 백오프는 max_backoff로 capped + 지터(0..jitter 비율) 가산.
    slept: list[float] = []

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="err")  # 항상 실패 → 백오프 관찰

    with pytest.raises(httpx.HTTPStatusError):
        fetch_text(
            "http://x", {}, client=_client(handler), retries=6,
            backoff=1.0, max_backoff=4.0, jitter=0.5,
            sleep=slept.append, rng=lambda: 1.0,  # 지터 최대(×1.5)
        )
    # base = min(1·2^n, 4) = [1,2,4,4,4] (attempt 0..4, 마지막 5는 sleep 없음), 지터 ×1.5
    assert slept == [1.5, 3.0, 6.0, 6.0, 6.0]
    assert max(slept) <= 4.0 * (1.0 + 0.5)  # cap 준수


def test_fetch_text_rides_out_transient_with_default_retries() -> None:
    # 기본 retries(8)로 긴 일시 끊김을 라이드아웃 — 7회 실패 후 8회차 성공.
    state = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] < 8:
            raise httpx.ConnectError("network down", request=req)
        return httpx.Response(200, text=OK_BODY)

    out = fetch_text("http://x", {}, client=_client(handler), sleep=lambda _d: None)
    assert out == OK_BODY and state["n"] == 8


def test_paginate_collects_until_total() -> None:
    pages = {
        1: (["a", "b"], 5),
        2: (["c", "d"], 5),
        3: (["e"], 5),
    }
    seen: list[int] = []

    def fetch_page(page: int) -> tuple[list[str], int]:
        seen.append(page)
        return pages[page]

    out = paginate(fetch_page, num_of_rows=2)
    assert out == ["a", "b", "c", "d", "e"]
    assert seen == [1, 2, 3]


def test_paginate_stops_on_empty_page() -> None:
    def fetch_page(page: int) -> tuple[list[str], int]:
        return ([], 100) if page == 1 else (["x"], 100)

    assert paginate(fetch_page, num_of_rows=10) == []


# ───────── ensure_success: 빈응답 진위 (fix/rent-empty-ledger) ─────────


def test_ensure_success_passes_real_success() -> None:
    # resultCode 000 = 정상(진짜 빈 월 포함) → 통과.
    ensure_success(fromstring("<response><header><resultCode>000</resultCode></header></response>"))


def test_ensure_success_raises_on_error_code() -> None:
    body = "<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>22</returnReasonCode>" \
           "<errMsg>LIMIT</errMsg></cmmMsgHeader></OpenAPI_ServiceResponse>"
    with pytest.raises(PublicDataError):
        ensure_success(fromstring(body))


def test_ensure_success_raises_on_no_code_transient() -> None:
    # 코드 없는 응답(잘린/과부하) = transient → raise. 코드 없는 빈응답을 '성공'으로
    # 통과시켜 ledger에 '완료 0건' 박히던 버그 차단.
    with pytest.raises(PublicDataError):
        ensure_success(fromstring("<response><body><items></items></body></response>"))


# ───────── resolve_total_count: 빈 응답 진위 검증 ─────────


def test_resolve_total_count_items_present() -> None:
    assert resolve_total_count("1389", 100) == 1389
    assert resolve_total_count(None, 3) == 3  # totalCount 태그 없어도 items 있으면 폴백


def test_resolve_total_count_real_empty() -> None:
    # items 0 + totalCount 0 명시 = 진짜 빈 월(거래 없음) → 0 반환(record 유지).
    assert resolve_total_count("0", 0) == 0


def test_resolve_total_count_unconfirmed_empty_raises() -> None:
    # items 0 + totalCount 태그 없음/불일치 = 미확정(transient) → raise(완료 0건 박힘 방지).
    with pytest.raises(PublicDataError):
        resolve_total_count(None, 0)
    with pytest.raises(PublicDataError):
        resolve_total_count("1389", 0)  # 총건수 있다는데 page가 빔 → transient
