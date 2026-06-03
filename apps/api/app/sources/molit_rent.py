"""MOLIT 아파트 전월세 실거래 클라이언트 (P2-1 — 매매와 별도 데이터셋).

엔드포인트: 국토교통부_아파트 전월세 실거래가 자료
(RTMSDataSvcAptRent/getRTMSDataSvcAptRent). 매매(molit.py)와 분리 — `_http`/`_parse`는 공유.

응답 XML(라이브 검증 P2-1-live, 강남 202505 2076건): aptNm·umdNm(법정동명)·jibun·excluUseAr·
deposit(보증금 만원)·monthlyRent(월세 만원, 전세=0)·floor·buildYear·dealYear/Month/Day·
sggCd·contractType(신규/갱신, 일부 빈값). 적재·조인은 store/rent_transaction_repo·join_repo.

**매매(molit.py)와 다른 점(라이브 확정)**:
- 도로명 태그가 **`roadnm`(소문자)** — 매매 `roadNm`과 다름.
- **`umdCd` 없음** → bjd_code(sgg+umd 10자리) 못 만듦 → None. 조인은 법정동명(umdNm) 폴백으로 동작.
- jibun-level `bonbun`/`bubun` 없음(도로명용 roadnm*만) → `jibun` 문자열로 from_molit 폴백.
"""

from __future__ import annotations

from datetime import date
from xml.etree.ElementTree import Element, fromstring

import httpx
from pydantic import BaseModel

from . import _parse
from ._http import DEFAULT_TIMEOUT, ensure_success, fetch_text, paginate, resolve_total_count

BASE_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent"
DEFAULT_NUM_OF_ROWS = 100


class RentTrade(BaseModel):
    """한 건의 아파트 전월세 실거래. 금액 단위는 만원. 전세는 monthly_rent=0."""

    apt_name: str  # aptNm
    legal_dong: str  # umdNm (법정동)
    road_addr: str | None  # roadNm
    build_year: int | None  # buildYear (구 데이터 누락 가능)
    net_area: float  # excluUseAr (전용면적)
    deposit: int  # deposit (보증금, 만원)
    monthly_rent: int  # monthlyRent (월세, 만원 — 전세는 0)
    floor: int | None  # floor
    deal_date: date  # dealYear·dealMonth·dealDay
    contract_type: str | None  # contractType (계약구분: 신규|갱신)
    # 조인 보조 — 매매와 동형(narrowing). 구 데이터 누락 가능.
    sgg_cd: str | None  # sggCd
    umd_cd: str | None  # umdCd
    jibun: str | None  # jibun (지번 문자열)
    bonbun: str | None  # bonbun
    bubun: str | None  # bubun

    @property
    def bjd_code(self) -> str | None:
        """법정동코드 10자리 = sggCd+umdCd. 조인 narrowing 키(매매와 동일)."""
        if self.sgg_cd and self.umd_cd:
            return self.sgg_cd + self.umd_cd
        return None

    @property
    def rent_type(self) -> str:
        """파생: 월세 0 = 전세(jeonse), 그 외 월세(monthly)."""
        return "jeonse" if self.monthly_rent == 0 else "monthly"


class RentPage:
    """한 페이지 파싱 결과 — 유효 항목 + 전체 건수."""

    def __init__(self, items: list[RentTrade], total_count: int) -> None:
        self.items = items
        self.total_count = total_count


def _parse_item(item: Element) -> RentTrade:
    deal_date = date(
        _parse.to_int(_parse.required_text(item, "dealYear")),
        _parse.to_int(_parse.required_text(item, "dealMonth")),
        _parse.to_int(_parse.required_text(item, "dealDay")),
    )
    build_year = _parse.text(item, "buildYear")
    floor = _parse.text(item, "floor")
    return RentTrade(
        apt_name=_parse.required_text(item, "aptNm"),
        legal_dong=_parse.required_text(item, "umdNm"),
        road_addr=_parse.text(item, "roadnm"),  # 전월세는 소문자 roadnm (매매 roadNm과 다름)
        build_year=_parse.to_int(build_year) if build_year else None,
        net_area=_parse.to_float(_parse.required_text(item, "excluUseAr")),
        deposit=_parse.to_int(_parse.required_text(item, "deposit")),
        monthly_rent=_parse.to_int(_parse.required_text(item, "monthlyRent")),
        floor=_parse.to_int(floor) if floor else None,
        deal_date=deal_date,
        contract_type=_parse.text(item, "contractType"),
        sgg_cd=_parse.text(item, "sggCd"),
        umd_cd=_parse.text(item, "umdCd"),
        jibun=_parse.text(item, "jibun"),
        bonbun=_parse.text(item, "bonbun"),
        bubun=_parse.text(item, "bubun"),
    )


def parse_rent_trades(xml_text: str) -> RentPage:
    """전월세 XML → RentPage. 에러코드면 raise, malformed item은 그 행만 skip(graceful)."""
    root = fromstring(xml_text)
    ensure_success(root)

    items: list[RentTrade] = []
    for el in root.findall(".//item"):
        try:
            items.append(_parse_item(el))
        except (ValueError, TypeError):
            continue
    total_count = resolve_total_count(root.findtext(".//totalCount"), len(items))
    return RentPage(items=items, total_count=total_count)


def fetch_rent_trades(
    lawd_cd: str,
    deal_ym: str,
    *,
    api_key: str,
    client: httpx.Client | None = None,
    num_of_rows: int = DEFAULT_NUM_OF_ROWS,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
) -> list[RentTrade]:
    """지역코드(법정동 5자리) × 계약월(YYYYMM)의 전 페이지 전월세를 모아 반환.

    매매 fetch_trades와 동형 — 테스트는 client에 MockTransport 주입(라이브 불요).
    """

    def fetch_page(page: int) -> tuple[list[RentTrade], int]:
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
        parsed = parse_rent_trades(xml_text)
        return parsed.items, parsed.total_count

    return paginate(fetch_page, num_of_rows=num_of_rows)
