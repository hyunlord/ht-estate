"""MOLIT 전월세 — XML 파싱(보증금/월세/전세-월세/계약구분) + fetch(MockTransport). 키리스."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import httpx

from app.sources.molit_rent import fetch_rent_trades, parse_rent_trades

FixtureLoader = Callable[[str], str]


def test_parse_rent_jeonse_and_monthly(load_fixture: FixtureLoader) -> None:
    page = parse_rent_trades(load_fixture("molit_rent.xml"))
    assert page.total_count == 2
    assert len(page.items) == 2

    jeonse = page.items[0]  # 래미안대치팰리스 — 월세 0 = 전세
    assert jeonse.apt_name == "래미안대치팰리스"
    assert jeonse.legal_dong == "대치동"
    assert jeonse.deposit == 180000  # '180,000' 콤마 제거
    assert jeonse.monthly_rent == 0
    assert jeonse.rent_type == "jeonse"  # 파생: 월세 0 → 전세
    assert jeonse.contract_type == "신규"
    assert jeonse.deal_date == date(2025, 4, 10)
    assert jeonse.bjd_code == "1168010600"  # sggCd+umdCd

    monthly = page.items[1]  # 은마 — 월세 180 = 월세
    assert monthly.deposit == 5000
    assert monthly.monthly_rent == 180
    assert monthly.rent_type == "monthly"  # 파생: 월세 > 0 → 월세
    assert monthly.contract_type == "갱신"


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_rent_single_page(load_fixture: FixtureLoader) -> None:
    body = load_fixture("molit_rent.xml")
    client = _client(lambda _req: httpx.Response(200, text=body))
    trades = fetch_rent_trades("11680", "202504", api_key="dummy", client=client, num_of_rows=200)
    assert len(trades) == 2
    assert {t.rent_type for t in trades} == {"jeonse", "monthly"}


def test_fetch_rent_reuses_molit_error_envelope(load_fixture: FixtureLoader) -> None:
    # 매매와 같은 _http/ensure_success 경로 — 에러 엔벨로프 전파.
    from app.sources.errors import PublicDataError

    body = load_fixture("molit_error.xml")
    client = _client(lambda _req: httpx.Response(200, text=body))
    try:
        fetch_rent_trades("11680", "202504", api_key="dummy", client=client)
        raise AssertionError("에러 엔벨로프인데 raise 안 됨")
    except PublicDataError:
        pass
