"""K-apt 공동주택 클라이언트 — 단지 목록 + 단지 기본정보.

엔드포인트:
- 단지 목록제공 (AptListService3): 단지코드 열거 (시도/시군구 단위)
- 기본 정보제공 (AptBasisInfoServiceV3/getAphusBassInfoV3): 단지코드 → 기본정보

derived 파싱(has_gym 키워드·parking_ratio)은 OUT — T0-2 소관. 여기선 raw 필드만
타입드로 뽑는다. parking_total은 지상+지하의 단순 합(키워드 파싱과 무관).
"""

from __future__ import annotations

from datetime import date
from xml.etree.ElementTree import Element, fromstring

import httpx
from pydantic import BaseModel

from . import _parse
from ._http import DEFAULT_TIMEOUT, ensure_success, fetch_xml

LIST_TOTAL_URL = "https://apis.data.go.kr/1613000/AptListService3/getTotalAptList3"
LIST_SIDO_URL = "https://apis.data.go.kr/1613000/AptListService3/getSidoAptList3"
LIST_SIGUNGU_URL = "https://apis.data.go.kr/1613000/AptListService3/getSigunguAptList3"
INFO_URL = "https://apis.data.go.kr/1613000/AptBasisInfoServiceV3/getAphusBassInfoV3"
DEFAULT_NUM_OF_ROWS = 100


class ComplexRef(BaseModel):
    """단지 목록의 한 항목 — 단지코드 + 식별용 최소정보."""

    kapt_code: str  # kaptCode (= complex_id)
    name: str | None  # kaptName
    bjd_code: str | None  # bjdCode (법정동코드)
    sido: str | None  # as1
    sigungu: str | None  # as2


class ComplexInfo(BaseModel):
    """단지 기본정보 — provenance 채우기 전의 raw 필드(파생 아님)."""

    kapt_code: str
    name: str | None
    legal_addr: str | None  # kaptAddr (지번주소)
    road_addr: str | None  # doroJuso (도로명주소)
    approval_date: date | None  # kaptUsedate (사용승인일)
    household_count: int | None  # kaptdaCnt (세대수)
    parking_total: int | None  # 지상+지하 합
    parking_ground: int | None  # kaptdPcnt
    parking_underground: int | None  # kaptdPcntu
    corridor_type: str | None  # codeHallNm (계단식/복도식/혼합식)
    building_type: str | None  # codeStr (건물구조)
    amenities_raw: str | None  # welfareFacility (부대복리시설 원본)


def _parse_ref(item: Element) -> ComplexRef:
    return ComplexRef(
        kapt_code=_parse.required_text(item, "kaptCode"),
        name=_parse.text(item, "kaptName"),
        bjd_code=_parse.text(item, "bjdCode"),
        sido=_parse.text(item, "as1"),
        sigungu=_parse.text(item, "as2"),
    )


def parse_complex_list(xml_text: str) -> list[ComplexRef]:
    """단지 목록 XML → ComplexRef 리스트. kaptCode 없는 항목은 skip(graceful)."""
    root = fromstring(xml_text)
    ensure_success(root)
    refs: list[ComplexRef] = []
    for el in root.findall(".//item"):
        try:
            refs.append(_parse_ref(el))
        except (ValueError, TypeError):
            continue
    return refs


def parse_complex_info(xml_text: str) -> ComplexInfo | None:
    """기본정보 XML → ComplexInfo. item 없거나 kaptCode 없으면 None."""
    root = fromstring(xml_text)
    ensure_success(root)
    item = root.find(".//item")
    if item is None:
        return None

    ground = _parse.opt_int(item, "kaptdPcnt")
    underground = _parse.opt_int(item, "kaptdPcntu")
    total = ground + underground if ground is not None and underground is not None else None

    try:
        kapt_code = _parse.required_text(item, "kaptCode")
    except ValueError:
        return None

    return ComplexInfo(
        kapt_code=kapt_code,
        name=_parse.text(item, "kaptName"),
        legal_addr=_parse.text(item, "kaptAddr"),
        road_addr=_parse.text(item, "doroJuso"),
        approval_date=_parse.yyyymmdd_to_date(_parse.text(item, "kaptUsedate")),
        household_count=_parse.opt_int(item, "kaptdaCnt"),
        parking_total=total,
        parking_ground=ground,
        parking_underground=underground,
        corridor_type=_parse.text(item, "codeHallNm"),
        building_type=_parse.text(item, "codeStr"),
        amenities_raw=_parse.text(item, "welfareFacility"),
    )


def list_complexes(
    *,
    api_key: str,
    sido: str | None = None,
    sigungu: str | None = None,
    client: httpx.Client | None = None,
    num_of_rows: int = DEFAULT_NUM_OF_ROWS,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
) -> list[ComplexRef]:
    """단지코드 리스트. 인자에 따라 전체/시도/시군구 엔드포인트 선택, 전 페이지 수집."""
    params: dict[str, str | int] = {"serviceKey": api_key, "numOfRows": num_of_rows}
    if sigungu is not None:
        url = LIST_SIGUNGU_URL
        params["sigunguCode"] = sigungu
    elif sido is not None:
        url = LIST_SIDO_URL
        params["sidoCode"] = sido
    else:
        url = LIST_TOTAL_URL

    collected: list[ComplexRef] = []
    page = 1
    while True:
        params["pageNo"] = page
        xml_text = fetch_xml(url, params, client=client, timeout=timeout)
        root = fromstring(xml_text)
        ensure_success(root)
        refs = [
            ref
            for el in root.findall(".//item")
            if (ref := _safe_ref(el)) is not None
        ]
        collected.extend(refs)
        total_text = root.findtext(".//totalCount")
        total = _parse.to_int(total_text) if total_text and total_text.strip() else len(collected)
        if not refs or page * num_of_rows >= total:
            break
        page += 1
    return collected


def _safe_ref(item: Element) -> ComplexRef | None:
    try:
        return _parse_ref(item)
    except (ValueError, TypeError):
        return None


def fetch_complex_info(
    kapt_code: str,
    *,
    api_key: str,
    client: httpx.Client | None = None,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
) -> ComplexInfo | None:
    """단지코드 → 기본정보. 응답에 단지가 없으면 None."""
    xml_text = fetch_xml(
        INFO_URL,
        {"serviceKey": api_key, "kaptCode": kapt_code},
        client=client,
        timeout=timeout,
    )
    return parse_complex_info(xml_text)
