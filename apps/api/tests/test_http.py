"""_http 재시도·페이지네이션 단위 테스트 — backoff=0으로 즉시(슬립 없이)."""

from __future__ import annotations

import httpx
import pytest

from app.sources._http import fetch_xml, paginate

OK_BODY = "<response><header><resultCode>00</resultCode></header></response>"


def _client(handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_xml_retries_5xx_then_succeeds() -> None:
    state = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] < 3:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, text=OK_BODY)

    out = fetch_xml("http://x", {}, client=_client(handler), retries=3, backoff=0.0)
    assert out == OK_BODY
    assert state["n"] == 3


def test_fetch_xml_retries_transport_error_then_succeeds() -> None:
    state = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] < 2:
            raise httpx.ConnectError("boom", request=req)
        return httpx.Response(200, text=OK_BODY)

    out = fetch_xml("http://x", {}, client=_client(handler), retries=3, backoff=0.0)
    assert out == OK_BODY
    assert state["n"] == 2


def test_fetch_xml_exhausts_retries_and_raises() -> None:
    state = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(500, text="err")

    with pytest.raises(httpx.HTTPStatusError):
        fetch_xml("http://x", {}, client=_client(handler), retries=2, backoff=0.0)
    assert state["n"] == 2  # 정확히 retries회만 시도


def test_fetch_xml_4xx_immediate_no_retry() -> None:
    state = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(404, text="not found")

    with pytest.raises(httpx.HTTPStatusError):
        fetch_xml("http://x", {}, client=_client(handler), retries=3, backoff=0.0)
    assert state["n"] == 1


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
