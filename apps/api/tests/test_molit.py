"""MOLIT 실거래 — 실캡처 XML(영문 태그) 파싱 + fetch(MockTransport).

fixture molit_trades.xml는 라이브 실응답 캡처(강남구 11680, 2025-04). molit_error는
시스템 에러 엔벨로프, molit_malformed/empty는 엣지 검증용 구성본.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import httpx
import pytest

from app.sources.errors import PublicDataError
from app.sources.molit import fetch_trades, parse_trades

FixtureLoader = Callable[[str], str]


def test_parse_trades_happy_real_capture(load_fixture: FixtureLoader) -> None:
    page = parse_trades(load_fixture("molit_trades.xml"))
    assert page.total_count == 128  # 실응답 totalCount
    assert len(page.items) == 3

    first = page.items[0]
    assert first.apt_name == "한양3"  # aptNm
    assert first.legal_dong == "압구정동"  # umdNm
    assert first.road_addr == "압구정로"  # roadNm
    assert first.build_year == 1978  # buildYear
    assert first.net_area == 161.9  # excluUseAr
    assert first.price == 700000  # dealAmount '700,000' 콤마 제거
    assert first.floor == 9
    assert first.deal_date == date(2025, 4, 1)  # dealYear/Month/Day
    # 식별 보조 필드(txn_id·조인용)
    assert first.sgg_cd == "11680"
    assert first.apt_seq == "11680-380"
    assert first.jibun == "489"


def test_parse_trades_empty(load_fixture: FixtureLoader) -> None:
    page = parse_trades(load_fixture("molit_empty.xml"))
    assert page.items == []
    assert page.total_count == 0


def test_parse_trades_skips_malformed_rows(load_fixture: FixtureLoader) -> None:
    page = parse_trades(load_fixture("molit_malformed.xml"))
    # 3개 중 정상 1개만, excluUseAr 누락·dealAmount 비숫자는 skip
    assert len(page.items) == 1
    assert page.items[0].apt_name == "정상단지"
    assert page.total_count == 3


def test_parse_trades_raises_on_error_envelope(load_fixture: FixtureLoader) -> None:
    with pytest.raises(PublicDataError) as exc_info:
        parse_trades(load_fixture("molit_error.xml"))
    assert exc_info.value.result_code == "30"


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_trades_single_page(load_fixture: FixtureLoader) -> None:
    body = load_fixture("molit_trades.xml")
    client = _client(lambda _req: httpx.Response(200, text=body))
    # num_of_rows=200 ≥ totalCount(128) → page1에서 정지
    trades = fetch_trades("11680", "202504", api_key="dummy", client=client, num_of_rows=200)
    assert len(trades) == 3


def test_fetch_trades_stops_on_empty_page(load_fixture: FixtureLoader) -> None:
    pages = [load_fixture("molit_trades.xml"), load_fixture("molit_empty.xml")]
    state = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        idx = min(state["n"], len(pages) - 1)
        state["n"] += 1
        return httpx.Response(200, text=pages[idx])

    trades = fetch_trades(
        "11680", "202504", api_key="dummy", client=_client(handler), num_of_rows=1
    )
    assert len(trades) == 3  # page1의 3건
    assert state["n"] == 2  # page2(빈)에서 정지


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
    assert state["n"] == 1
