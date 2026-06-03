"""MOLIT 비-아파트 전월세 실거래 클라이언트 — 연립다세대(RH)·오피스텔(Offi). (P5-1b-2)

STEP 1 라이브 검증(docs/p5-1b-step1-fields.md)으로 필드 확정 — **추정 아님**:
- RH `RTMSDataSvcRHRent`: 건물명 `mhouseNm` · `excluUseAr`(전용) · `jibun`·`umdNm`·`sggCd` ·
  `deposit`/`monthlyRent` · floor/buildYear/contractType.
- Offi `RTMSDataSvcOffiRent`: 건물명 `offiNm` · 그 외 동형 + `sggNm`.
- **roadNm 없음**(아파트 전월세와 다름) → building_key는 PNU식(법정동+지번+건물명, nonapt_repo).
- SH(단독)는 건물명·jibun 없음(연면적뿐) → 지도 부적합 → **제외**(이 모듈 미포함).
- 매매(RHTrade/OffiTrade)는 키 미인증(403) → 별도(승인 후 P5-1b-3).

`_http`/`_parse`는 아파트 전월세(molit_rent)와 공유. 적재·건물도출은 store/nonapt_repo.
"""

from __future__ import annotations

from datetime import date
from typing import Literal
from xml.etree.ElementTree import Element, fromstring

import httpx
from pydantic import BaseModel

from . import _parse
from ._http import DEFAULT_TIMEOUT, ensure_success, fetch_text, paginate, resolve_total_count

NonAptKind = Literal["rowhouse", "officetel"]

# kind → (엔드포인트, 건물명 태그, property_type). STEP 1 검증 경로.
_KIND: dict[str, dict[str, str]] = {
    "rowhouse": {
        "url": "https://apis.data.go.kr/1613000/RTMSDataSvcRHRent/getRTMSDataSvcRHRent",
        "name_tag": "mhouseNm",
        "property_type": "rowhouse",
    },
    "officetel": {
        "url": "https://apis.data.go.kr/1613000/RTMSDataSvcOffiRent/getRTMSDataSvcOffiRent",
        "name_tag": "offiNm",
        "property_type": "officetel",
    },
}
DEFAULT_NUM_OF_ROWS = 100


class NonAptRentTrade(BaseModel):
    """비-아파트(연립·오피스텔) 전월세 1건. 금액 만원, 전세=월세0. net_area=전용(excluUseAr)."""

    property_type: NonAptKind  # rowhouse | officetel
    name: str  # mhouseNm | offiNm (건물명)
    legal_dong: str  # umdNm (법정동명)
    jibun: str | None  # jibun (지번 문자열) — roadNm 없음
    net_area: float  # excluUseAr (전용면적 ㎡)
    deposit: int  # deposit (보증금, 만원)
    monthly_rent: int  # monthlyRent (월세, 만원 — 전세=0)
    floor: int | None  # floor
    build_year: int | None  # buildYear
    contract_type: str | None  # contractType (신규|갱신)
    sgg_cd: str | None  # sggCd (5자리)
    sgg_nm: str | None  # sggNm (Offi만 — 시군구명)
    deal_date: date

    @property
    def rent_type(self) -> str:
        return "jeonse" if self.monthly_rent == 0 else "monthly"


class NonAptRentPage:
    def __init__(self, items: list[NonAptRentTrade], total_count: int) -> None:
        self.items = items
        self.total_count = total_count


def _parse_item(item: Element, kind: NonAptKind) -> NonAptRentTrade:
    cfg = _KIND[kind]
    deal_date = date(
        _parse.to_int(_parse.required_text(item, "dealYear")),
        _parse.to_int(_parse.required_text(item, "dealMonth")),
        _parse.to_int(_parse.required_text(item, "dealDay")),
    )
    build_year = _parse.text(item, "buildYear")
    floor = _parse.text(item, "floor")
    return NonAptRentTrade(
        property_type=kind,
        name=_parse.required_text(item, cfg["name_tag"]),
        legal_dong=_parse.required_text(item, "umdNm"),
        jibun=_parse.text(item, "jibun"),
        net_area=_parse.to_float(_parse.required_text(item, "excluUseAr")),
        deposit=_parse.to_int(_parse.required_text(item, "deposit")),
        monthly_rent=_parse.to_int(_parse.required_text(item, "monthlyRent")),
        floor=_parse.to_int(floor) if floor else None,
        build_year=_parse.to_int(build_year) if build_year else None,
        contract_type=_parse.text(item, "contractType"),
        sgg_cd=_parse.text(item, "sggCd"),
        sgg_nm=_parse.text(item, "sggNm"),
        deal_date=deal_date,
    )


def parse_nonapt_rent(xml_text: str, kind: NonAptKind) -> NonAptRentPage:
    """비-아파트 전월세 XML → 페이지. 에러코드 raise, malformed item은 그 행만 skip(graceful)."""
    root = fromstring(xml_text)
    ensure_success(root)
    items: list[NonAptRentTrade] = []
    for el in root.findall(".//item"):
        try:
            items.append(_parse_item(el, kind))
        except (ValueError, TypeError):
            continue
    total_count = resolve_total_count(root.findtext(".//totalCount"), len(items))
    return NonAptRentPage(items=items, total_count=total_count)


def fetch_nonapt_rent(
    lawd_cd: str,
    deal_ym: str,
    *,
    kind: NonAptKind,
    api_key: str,
    client: httpx.Client | None = None,
    num_of_rows: int = DEFAULT_NUM_OF_ROWS,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
) -> list[NonAptRentTrade]:
    """지역코드 × 계약월의 전 페이지 비-아파트 전월세. kind로 RH/Offi 선택(동형 페이지네이션).

    테스트는 client에 MockTransport 주입(라이브 불요). 매매는 키 미인증(403) → 미포함.
    """
    url = _KIND[kind]["url"]

    def fetch_page(page: int) -> tuple[list[NonAptRentTrade], int]:
        xml_text = fetch_text(
            url,
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
        parsed = parse_nonapt_rent(xml_text, kind)
        return parsed.items, parsed.total_count

    return paginate(fetch_page, num_of_rows=num_of_rows)
