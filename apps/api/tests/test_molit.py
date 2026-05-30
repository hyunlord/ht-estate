"""MOLIT 실거래 클라이언트 — 파싱(happy/empty/malformed/error) + fetch(MockTransport)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import httpx
import pytest

from app.sources.errors import PublicDataError
from app.sources.molit import fetch_trades, parse_trades

FixtureLoader = Callable[[str], str]


def test_parse_trades_happy(load_fixture: FixtureLoader) -> None:
    page = parse_trades(load_fixture("molit_trades.xml"))
    assert page.total_count == 2
    assert len(page.items) == 2

    first = page.items[0]
    assert first.apt_name == "래미안위브"
    assert first.legal_dong == "도곡동"  # 앞 공백 strip
    assert first.road_addr == "역삼로 405"
    assert first.build_year == 2015
    assert first.net_area == 84.97
    assert first.price == 142000  # '142,000' 콤마 제거
    assert first.floor == 12
    assert first.deal_date == date(2025, 4, 15)


def test_parse_trades_empty(load_fixture: FixtureLoader) -> None:
    page = parse_trades(load_fixture("molit_empty.xml"))
    assert page.items == []
    assert page.total_count == 0


def test_parse_trades_skips_malformed_rows(load_fixture: FixtureLoader) -> None:
    page = parse_trades(load_fixture("molit_malformed.xml"))
    # 3개 중 정상 1개만 남고, 전용면적 누락·거래금액 비숫자는 skip
    assert len(page.items) == 1
    assert page.items[0].apt_name == "정상단지"
    assert page.total_count == 3  # totalCount는 응답값 보존


def test_parse_trades_raises_on_error_envelope(load_fixture: FixtureLoader) -> None:
    with pytest.raises(PublicDataError) as exc_info:
        parse_trades(load_fixture("molit_error.xml"))
    assert exc_info.value.result_code == "30"


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_trades_single_page(load_fixture: FixtureLoader) -> None:
    body = load_fixture("molit_trades.xml")
    client = _client(lambda _req: httpx.Response(200, text=body))
    trades = fetch_trades("11680", "202504", api_key="dummy", client=client)
    assert len(trades) == 2


def test_fetch_trades_stops_on_empty_page(load_fixture: FixtureLoader) -> None:
    pages = [load_fixture("molit_trades.xml"), load_fixture("molit_empty.xml")]
    state = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        idx = min(state["n"], len(pages) - 1)
        state["n"] += 1
        return httpx.Response(200, text=pages[idx])

    # num_of_rows=1로 페이지네이션 강제 → page1(2건) 후 page2(빈) 만나 정지
    trades = fetch_trades(
        "11680", "202504", api_key="dummy", client=_client(handler), num_of_rows=1
    )
    assert len(trades) == 2
    assert state["n"] == 2  # 정확히 2페이지 호출


def test_fetch_trades_propagates_error(load_fixture: FixtureLoader) -> None:
    body = load_fixture("molit_error.xml")
    client = _client(lambda _req: httpx.Response(200, text=body))
    with pytest.raises(PublicDataError):
        fetch_trades("11680", "202504", api_key="dummy", client=client)


def test_fetch_trades_4xx_raises_without_retry() -> None:
    state = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(404, text="not found")

    with pytest.raises(httpx.HTTPStatusError):
        fetch_trades("11680", "202504", api_key="dummy", client=_client(handler))
    assert state["n"] == 1  # 4xx는 재시도 안 함
