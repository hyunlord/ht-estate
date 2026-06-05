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


class NonAptTradeBase(BaseModel):
    """비-아파트(연립·오피스텔) 거래 공통 — 건물 식별·도출용. 전월세·매매가 공유.

    building_key(PNU)·geocode 주소·건물 upsert가 이 필드들만 쓰므로, 전월세/매매 둘 다
    같은 건물에 합류한다(좌표 재사용·재geocode 0).
    """

    property_type: NonAptKind  # rowhouse | officetel
    name: str  # mhouseNm | offiNm (건물명)
    legal_dong: str  # umdNm (법정동명)
    jibun: str | None  # jibun (지번 문자열) — roadNm 없음
    net_area: float  # excluUseAr (전용면적 ㎡)
    floor: int | None  # floor
    build_year: int | None  # buildYear
    sgg_cd: str | None  # sggCd (5자리)
    sgg_nm: str | None  # sggNm (Offi만 — 시군구명)
    deal_date: date


class NonAptRentTrade(NonAptTradeBase):
    """비-아파트 전월세 1건. 금액 만원, 전세=월세0."""

    deposit: int  # deposit (보증금, 만원)
    monthly_rent: int  # monthlyRent (월세, 만원 — 전세=0)
    contract_type: str | None  # contractType (신규|갱신)

    @property
    def rent_type(self) -> str:
        return "jeonse" if self.monthly_rent == 0 else "monthly"


class NonAptSaleTrade(NonAptTradeBase):
    """비-아파트 매매 1건(RHTrade/OffiTrade). price=dealAmount(만원). 취소거래는 파싱서 제외."""

    price: int  # dealAmount (거래금액, 만원)


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


# ─────────────────────────── 매매(P5-1b-3) ───────────────────────────
# 활용신청 승인 확정(라이브 프로브 resultCode 000): RHTrade 15126467 · OffiTrade 15126464.
# 전월세와 동형 필드 + dealAmount(거래금액) · cdealType(해제여부 'O'=취소→제외).
_KIND_SALE: dict[str, dict[str, str]] = {
    "rowhouse": {
        "url": "https://apis.data.go.kr/1613000/RTMSDataSvcRHTrade/getRTMSDataSvcRHTrade",
        "name_tag": "mhouseNm",
    },
    "officetel": {
        "url": "https://apis.data.go.kr/1613000/RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade",
        "name_tag": "offiNm",
    },
}


class NonAptSalePage:
    def __init__(self, items: list[NonAptSaleTrade], total_count: int) -> None:
        self.items = items
        self.total_count = total_count


def _is_cancelled(item: Element) -> bool:
    """해제(취소)거래 여부 — cdealType='O'면 취소. 취소건은 적재 금지(STEP 프로브 필드)."""
    return (_parse.text(item, "cdealType") or "").strip().upper() == "O"


def _parse_sale_item(item: Element, kind: NonAptKind) -> NonAptSaleTrade:
    cfg = _KIND_SALE[kind]
    deal_date = date(
        _parse.to_int(_parse.required_text(item, "dealYear")),
        _parse.to_int(_parse.required_text(item, "dealMonth")),
        _parse.to_int(_parse.required_text(item, "dealDay")),
    )
    build_year = _parse.text(item, "buildYear")
    floor = _parse.text(item, "floor")
    return NonAptSaleTrade(
        property_type=kind,
        name=_parse.required_text(item, cfg["name_tag"]),
        legal_dong=_parse.required_text(item, "umdNm"),
        jibun=_parse.text(item, "jibun"),
        net_area=_parse.to_float(_parse.required_text(item, "excluUseAr")),
        price=_parse.to_int(_parse.required_text(item, "dealAmount")),
        floor=_parse.to_int(floor) if floor else None,
        build_year=_parse.to_int(build_year) if build_year else None,
        sgg_cd=_parse.text(item, "sggCd"),
        sgg_nm=_parse.text(item, "sggNm"),
        deal_date=deal_date,
    )


def parse_nonapt_sale(xml_text: str, kind: NonAptKind) -> NonAptSalePage:
    """비-아파트 매매 XML → 페이지. 에러코드 raise, 취소(cdealType='O')·malformed item은 제외.

    ⚠ transient 빈응답 가드(`resolve_total_count`)는 **원시 `<item>` 존재 여부**로 판정한다(취소
    필터 *전*). 거래 적은 군지역에서 한 월의 거래가 전부 취소면 필터 후 0건이라, 필터 후 카운트로
    가드하면 totalCount>0·items=0 → transient 오판 raise → 그 region이 영원히 미적재된다(실측:
    46860 보성군 202602 RH totalCount=1·전부취소). 원시 item이 하나라도 있으면 API가 응답한
    것이므로 비-transient → 취소 걸러 빈 페이지 반환. 진짜 burst 빈응답(원시 item 0)만 raise.
    """
    root = fromstring(xml_text)
    ensure_success(root)
    raw_items = root.findall(".//item")
    items: list[NonAptSaleTrade] = []
    for el in raw_items:
        if _is_cancelled(el):
            continue  # 취소거래 제외(적재 금지)
        try:
            items.append(_parse_sale_item(el, kind))
        except (ValueError, TypeError):
            continue
    # 가드는 원시 item 수로 — 취소/malformed로 0이 돼도 transient 아님(API가 데이터를 줌).
    total_count = resolve_total_count(root.findtext(".//totalCount"), len(raw_items))
    return NonAptSalePage(items=items, total_count=total_count)


def fetch_nonapt_sale(
    lawd_cd: str,
    deal_ym: str,
    *,
    kind: NonAptKind,
    api_key: str,
    client: httpx.Client | None = None,
    num_of_rows: int = DEFAULT_NUM_OF_ROWS,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
) -> list[NonAptSaleTrade]:
    """지역코드 × 거래월의 전 페이지 비-아파트 매매(취소 제외). kind로 RH/Offi 선택(동형)."""
    url = _KIND_SALE[kind]["url"]

    def fetch_page(page: int) -> tuple[list[NonAptSaleTrade], int]:
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
        parsed = parse_nonapt_sale(xml_text, kind)
        return parsed.items, parsed.total_count

    return paginate(fetch_page, num_of_rows=num_of_rows)
