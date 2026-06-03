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
    assert jeonse.road_addr == "삼성로"  # 전월세 소문자 roadnm (P2-1-live 보정)
    assert jeonse.deposit == 180000  # '180,000' 콤마 제거
    assert jeonse.monthly_rent == 0
    assert jeonse.rent_type == "jeonse"  # 파생: 월세 0 → 전세
    assert jeonse.contract_type == "신규"
    assert jeonse.deal_date == date(2025, 5, 10)
    # 전월세 API는 umdCd 미제공 → bjd_code None(조인은 법정동명 폴백). 라이브 확정.
    assert jeonse.bjd_code is None
    assert jeonse.jibun == "1027"

    monthly = page.items[1]  # 은마 — 월세 180 = 월세
    assert monthly.deposit == 5000
    assert monthly.monthly_rent == 180
    assert monthly.rent_type == "monthly"  # 파생: 월세 > 0 → 월세
    assert monthly.contract_type is None  # 빈 contractType → None(라이브 178건 사례)


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


def test_parse_rent_real_empty_returns_empty(load_fixture: FixtureLoader) -> None:
    # 진짜 빈 월(resultCode 000 + totalCount 0) → 빈 페이지(0건). record 대상(거래 없는 월).
    page = parse_rent_trades(load_fixture("molit_empty.xml"))
    assert page.items == []
    assert page.total_count == 0


def test_parse_rent_burst_empty_raises(load_fixture: FixtureLoader) -> None:
    # fix/rent-empty-ledger: resultCode 000이나 totalCount 없는 빈응답(버스트) → raise.
    # silent 0건 → ledger '완료' 박힘 방지(transient → pending 유지).
    import pytest

    from app.sources.errors import PublicDataError

    with pytest.raises(PublicDataError):
        parse_rent_trades(load_fixture("molit_burst_empty.xml"))


def test_parse_rent_nocode_raises(load_fixture: FixtureLoader) -> None:
    # 코드 자체가 없는 잘린 응답(transient) → raise.
    import pytest

    from app.sources.errors import PublicDataError

    with pytest.raises(PublicDataError):
        parse_rent_trades(load_fixture("molit_nocode.xml"))
