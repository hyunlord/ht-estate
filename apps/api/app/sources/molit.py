"""MOLIT 아파트 매매 실거래가 상세 자료 클라이언트.

엔드포인트: 국토교통부_아파트 매매 실거래가 상세 자료
(RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev) — 응답은 한글 XML 태그.

이 티켓(T0-1)은 파싱·페이지네이션·에러 처리까지. 실제 DB 적재(txn_id 생성·
저장)와 단지 조인(match_confidence)은 T0-3·T0-4 소관이라 Trade에 txn_id 없음.
"""

from __future__ import annotations

from datetime import date
from xml.etree.ElementTree import Element, fromstring

import httpx
from pydantic import BaseModel

from . import _parse
from ._http import DEFAULT_TIMEOUT, ensure_success, fetch_xml

BASE_URL = (
    "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
)
DEFAULT_NUM_OF_ROWS = 100


class Trade(BaseModel):
    """한 건의 아파트 매매 실거래(파싱 결과). 가격 단위는 만원(MOLIT 원단위)."""

    apt_name: str  # 아파트
    legal_dong: str  # 법정동
    road_addr: str | None  # 도로명 (구 데이터는 비어있을 수 있음)
    build_year: int  # 건축년도
    net_area: float  # 전용면적(㎡)
    price: int  # 거래금액(만원)
    floor: int  # 층
    deal_date: date  # 년·월·일 조합


class TradePage:
    """한 페이지 파싱 결과 — 유효 항목 + 전체 건수(페이지네이션용)."""

    def __init__(self, items: list[Trade], total_count: int) -> None:
        self.items = items
        self.total_count = total_count


def _parse_item(item: Element) -> Trade:
    deal_date = date(
        _parse.to_int(_parse.required_text(item, "년")),
        _parse.to_int(_parse.required_text(item, "월")),
        _parse.to_int(_parse.required_text(item, "일")),
    )
    return Trade(
        apt_name=_parse.required_text(item, "아파트"),
        legal_dong=_parse.required_text(item, "법정동"),
        road_addr=_parse.text(item, "도로명"),
        build_year=_parse.to_int(_parse.required_text(item, "건축년도")),
        net_area=_parse.to_float(_parse.required_text(item, "전용면적")),
        price=_parse.to_int(_parse.required_text(item, "거래금액")),
        floor=_parse.to_int(_parse.required_text(item, "층")),
        deal_date=deal_date,
    )


def parse_trades(xml_text: str) -> TradePage:
    """상세자료 XML → TradePage. 에러코드면 raise, 빈 응답이면 빈 리스트.

    개별 item이 malformed(필수필드 누락·비숫자)면 그 행만 skip하고 진행(graceful).
    """
    root = fromstring(xml_text)
    ensure_success(root)

    items: list[Trade] = []
    for el in root.findall(".//item"):
        try:
            items.append(_parse_item(el))
        except (ValueError, TypeError):
            continue  # malformed row → skip, 나머지는 보존

    total_text = root.findtext(".//totalCount")
    total_count = _parse.to_int(total_text) if total_text and total_text.strip() else len(items)
    return TradePage(items=items, total_count=total_count)


def fetch_trades(
    lawd_cd: str,
    deal_ym: str,
    *,
    api_key: str,
    client: httpx.Client | None = None,
    num_of_rows: int = DEFAULT_NUM_OF_ROWS,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
) -> list[Trade]:
    """지역코드(법정동 5자리) × 계약월(YYYYMM)의 전 페이지 실거래를 모아 반환.

    `api_key`는 decoded 서비스키(settings.get_api_key()). 테스트는 `client`에
    MockTransport를 주입해 라이브 호출 없이 검증한다.
    """
    collected: list[Trade] = []
    page = 1
    while True:
        xml_text = fetch_xml(
            BASE_URL,
            {
                "serviceKey": api_key,
                "LAWD_CD": lawd_cd,
                "DEAL_YMD": deal_ym,
                "pageNo": page,
                "numOfRows": num_of_rows,
            },
            client=client,
            timeout=timeout,
        )
        parsed = parse_trades(xml_text)
        collected.extend(parsed.items)
        if not parsed.items or page * num_of_rows >= parsed.total_count:
            break
        page += 1
    return collected
