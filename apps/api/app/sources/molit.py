"""MOLIT 아파트 매매 실거래가 상세 자료 클라이언트.

엔드포인트: 국토교통부_아파트 매매 실거래가 상세 자료
(RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev).

응답은 **XML + 영문 camelCase 태그**다(라이브 검증 T0-2에서 확정 — 한글 태그 아님):
aptNm·umdNm(법정동)·roadNm·buildYear·excluUseAr(전용)·dealAmount(만원)·floor·
dealYear/dealMonth/dealDay. 실거래 DB 적재(txn_id 생성)·단지 조인은 T0-3·T0-4 소관.
"""

from __future__ import annotations

from datetime import date
from xml.etree.ElementTree import Element, fromstring

import httpx
from pydantic import BaseModel

from . import _parse
from ._http import DEFAULT_TIMEOUT, ensure_success, fetch_text, paginate

BASE_URL = (
    "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
)
DEFAULT_NUM_OF_ROWS = 100


class Trade(BaseModel):
    """한 건의 아파트 매매 실거래(파싱 결과). 가격 단위는 만원(MOLIT 원단위)."""

    apt_name: str  # aptNm
    legal_dong: str  # umdNm (읍면동명 = 법정동)
    road_addr: str | None  # roadNm (구 데이터는 비어있을 수 있음)
    build_year: int  # buildYear
    net_area: float  # excluUseAr (전용면적, ㎡)
    price: int  # dealAmount (만원)
    floor: int  # floor
    deal_date: date  # dealYear·dealMonth·dealDay 조합
    # 식별 보조 필드(txn_id 구성·T0-4 조인용). 구 데이터엔 빠질 수 있어 optional.
    sgg_cd: str | None  # sggCd (시군구코드 5자리)
    umd_cd: str | None  # umdCd (읍면동코드 5자리)
    apt_seq: str | None  # aptSeq (단지 일련, 예 '11680-380')
    jibun: str | None  # jibun (지번)
    # 지번 매칭용(T0-4c) — 0패딩 4자리 본번/부번. 구 데이터는 빠질 수 있어 optional.
    bonbun: str | None  # bonbun (본번, 예 '0489')
    bubun: str | None  # bubun (부번, 예 '0000')

    @property
    def bjd_code(self) -> str | None:
        """법정동코드 10자리 = sggCd+umdCd (= K-apt bjdCode). 조인 narrowing 키."""
        if self.sgg_cd and self.umd_cd:
            return self.sgg_cd + self.umd_cd
        return None


class TradePage:
    """한 페이지 파싱 결과 — 유효 항목 + 전체 건수(페이지네이션용)."""

    def __init__(self, items: list[Trade], total_count: int) -> None:
        self.items = items
        self.total_count = total_count


def _parse_item(item: Element) -> Trade:
    deal_date = date(
        _parse.to_int(_parse.required_text(item, "dealYear")),
        _parse.to_int(_parse.required_text(item, "dealMonth")),
        _parse.to_int(_parse.required_text(item, "dealDay")),
    )
    return Trade(
        apt_name=_parse.required_text(item, "aptNm"),
        legal_dong=_parse.required_text(item, "umdNm"),
        road_addr=_parse.text(item, "roadNm"),
        build_year=_parse.to_int(_parse.required_text(item, "buildYear")),
        net_area=_parse.to_float(_parse.required_text(item, "excluUseAr")),
        price=_parse.to_int(_parse.required_text(item, "dealAmount")),
        floor=_parse.to_int(_parse.required_text(item, "floor")),
        deal_date=deal_date,
        sgg_cd=_parse.text(item, "sggCd"),
        umd_cd=_parse.text(item, "umdCd"),
        apt_seq=_parse.text(item, "aptSeq"),
        jibun=_parse.text(item, "jibun"),
        bonbun=_parse.text(item, "bonbun"),
        bubun=_parse.text(item, "bubun"),
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
    def fetch_page(page: int) -> tuple[list[Trade], int]:
        xml_text = fetch_text(
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
        return parsed.items, parsed.total_count

    return paginate(fetch_page, num_of_rows=num_of_rows)
