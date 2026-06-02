"""K-apt 공동주택 클라이언트 — 단지 목록 + 단지 기본정보.

응답 포맷은 **JSON**이다(라이브 검증 T0-2에서 확정 — XML 아님). 또한 단지 정보가
두 V4 엔드포인트로 쪼개져 있어 **병합**해야 필수필드가 다 채워진다:
- getAphusBassInfoV4 (기본): 사용승인일·세대수·복도유형·주소
- getAphusDtlInfoV4  (상세): 주차·건물구조·부대복리시설(raw)

derived 파싱(has_gym·parking_ratio)은 derive.py, 적재는 store/complex_repo.py.
여기선 raw 필드만 타입드로 뽑는다. parking_total은 지상+지하 단순합.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
from pydantic import BaseModel

from . import _parse
from ._http import DEFAULT_TIMEOUT, fetch_text, json_body, paginate

LIST_TOTAL_URL = "https://apis.data.go.kr/1613000/AptListService3/getTotalAptList3"
LIST_SIDO_URL = "https://apis.data.go.kr/1613000/AptListService3/getSidoAptList3"
LIST_SIGUNGU_URL = "https://apis.data.go.kr/1613000/AptListService3/getSigunguAptList3"
BASIS_URL = "https://apis.data.go.kr/1613000/AptBasisInfoServiceV4/getAphusBassInfoV4"
DETAIL_URL = "https://apis.data.go.kr/1613000/AptBasisInfoServiceV4/getAphusDtlInfoV4"
DEFAULT_NUM_OF_ROWS = 100


class ComplexRef(BaseModel):
    """단지 목록의 한 항목 — 단지코드 + 식별용 최소정보."""

    kapt_code: str  # kaptCode (= complex_id)
    name: str | None  # kaptName
    bjd_code: str | None  # bjdCode (법정동코드)
    sido: str | None  # as1
    sigungu: str | None  # as2


class ComplexInfo(BaseModel):
    """단지 기본정보 — basis+detail 병합, provenance 채우기 전의 raw 필드(파생 아님)."""

    kapt_code: str
    name: str | None
    bjd_code: str | None  # bjdCode (법정동코드 10자리) — 조인 narrowing
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
    # ── P4-1 풀필드 확장: V4 basis+detail에서 안 쓰던 구조화 필드 (NL 토대). 전부 nullable. ──
    # basis (getAphusBassInfoV4)
    heat_type: str | None  # codeHeatNm (난방방식: 지역난방/개별난방/중앙난방)
    sale_type: str | None  # codeSaleNm (분양형태: 분양/임대/혼합)
    mgmt_type: str | None  # codeMgrNm (관리방식: 위탁관리/자치관리)
    dong_count: int | None  # kaptDongCnt (동수)
    top_floor: int | None  # kaptTopFloor (최고층)
    priv_area: float | None  # privArea (전용면적 합, ㎡)
    mgmt_area: float | None  # kaptMarea (관리비부과면적, ㎡)
    builder: str | None  # kaptBcompany (건설사/시공사)
    developer: str | None  # kaptAcompany (시행사)
    # detail (getAphusDtlInfoV4)
    mgmt_staff: int | None  # kaptMgrCnt (관리인원)
    security_type: str | None  # codeSec (경비방식)
    security_staff: int | None  # kaptdScnt (경비인원)
    cleaning_type: str | None  # codeClean (청소방식)
    cleaning_staff: int | None  # kaptdClcnt (청소인원)
    disinfection_type: str | None  # codeDisinf (소독방식)
    disinfection_staff: int | None  # kaptdDcnt (소독인원)
    disinfection_method: str | None  # disposalType (소독방법: 도포식/분무식…)
    garbage_type: str | None  # codeGarbage (음식물처리)
    water_supply: str | None  # codeWsupply (급수방식)
    electricity_contract: str | None  # codeEcon (전기계약방식)
    fire_alarm: str | None  # codeFalarm (화재수신반방식)
    internet: str | None  # codeNet (인터넷망 유/무)
    elevator_count: int | None  # kaptdEcnt (승강기 대수)
    cctv_count: int | None  # kaptdCccnt (CCTV 대수)
    subway_line: str | None  # subwayLine (지하철 노선)
    subway_station: str | None  # subwayStation (지하철 역명)
    subway_time: str | None  # kaptdWtimesub (지하철까지 도보, 역세권)
    bus_time: str | None  # kaptdWtimebus (버스정류장까지 도보)
    convenient_facility_raw: str | None  # convenientFacility (편의시설 원본)
    education_facility_raw: str | None  # educationFacility (교육시설 원본)


def _as_items(body: dict[str, Any]) -> list[dict[str, Any]]:
    """body.items를 리스트로 정규화. data.go.kr은 단건일 때 dict로 줄 수 있다."""
    items = body.get("items")
    if isinstance(items, dict):
        items = items.get("item")
    if items is None:
        return []
    if isinstance(items, dict):
        return [items]
    return [it for it in items if isinstance(it, dict)]


def _parse_ref(item: dict[str, Any]) -> ComplexRef | None:
    code = _parse.json_str(item.get("kaptCode"))
    if code is None:
        return None
    return ComplexRef(
        kapt_code=code,
        name=_parse.json_str(item.get("kaptName")),
        bjd_code=_parse.json_str(item.get("bjdCode")),
        sido=_parse.json_str(item.get("as1")),
        sigungu=_parse.json_str(item.get("as2")),
    )


def _parse_list_page(json_text: str) -> tuple[list[ComplexRef], int]:
    """목록 JSON → (ComplexRef 리스트, totalCount). kaptCode 없는 항목은 skip."""
    body = json_body(json_text)
    refs = [ref for it in _as_items(body) if (ref := _parse_ref(it)) is not None]
    total = _parse.json_int(body.get("totalCount"))
    return refs, total if total is not None else len(refs)


def parse_complex_list(json_text: str) -> list[ComplexRef]:
    """단지 목록 JSON → ComplexRef 리스트."""
    return _parse_list_page(json_text)[0]


def _single_item(json_text: str) -> dict[str, Any] | None:
    """단건 응답(body.item) 추출. 없으면 None."""
    item = json_body(json_text).get("item")
    return item if isinstance(item, dict) else None


def parse_complex_info(basis_json: str, detail_json: str) -> ComplexInfo | None:
    """기본(basis)+상세(detail) JSON 병합 → ComplexInfo. 둘 다 item 없으면 None."""
    basis = _single_item(basis_json) or {}
    detail = _single_item(detail_json) or {}
    if not basis and not detail:
        return None

    code = _parse.json_str(basis.get("kaptCode")) or _parse.json_str(detail.get("kaptCode"))
    if code is None:
        return None

    ground = _parse.json_int(detail.get("kaptdPcnt"))
    underground = _parse.json_int(detail.get("kaptdPcntu"))
    total = ground + underground if ground is not None and underground is not None else None

    return ComplexInfo(
        kapt_code=code,
        name=_parse.json_str(basis.get("kaptName")) or _parse.json_str(detail.get("kaptName")),
        bjd_code=_parse.json_str(basis.get("bjdCode")),
        legal_addr=_parse.json_str(basis.get("kaptAddr")),
        road_addr=_parse.json_str(basis.get("doroJuso")),
        approval_date=_parse.yyyymmdd_to_date(_parse.json_str(basis.get("kaptUsedate"))),
        household_count=_parse.json_int(basis.get("kaptdaCnt")),
        parking_total=total,
        parking_ground=ground,
        parking_underground=underground,
        corridor_type=_parse.json_str(basis.get("codeHallNm")),
        building_type=_parse.json_str(detail.get("codeStr")),
        amenities_raw=_parse.json_str(detail.get("welfareFacility")),
        # ── P4-1 풀필드 (실응답에 있는 것만 — 없으면 graceful None) ──
        # basis
        heat_type=_parse.json_str(basis.get("codeHeatNm")),
        sale_type=_parse.json_str(basis.get("codeSaleNm")),
        mgmt_type=_parse.json_str(basis.get("codeMgrNm")),
        dong_count=_parse.json_int(basis.get("kaptDongCnt")),
        top_floor=_parse.json_int(basis.get("kaptTopFloor")),
        priv_area=_parse.json_float(basis.get("privArea")),
        mgmt_area=_parse.json_float(basis.get("kaptMarea")),
        builder=_parse.json_str(basis.get("kaptBcompany")),
        developer=_parse.json_str(basis.get("kaptAcompany")),
        # detail
        mgmt_staff=_parse.json_int(detail.get("kaptMgrCnt")),
        security_type=_parse.json_str(detail.get("codeSec")),
        security_staff=_parse.json_int(detail.get("kaptdScnt")),
        cleaning_type=_parse.json_str(detail.get("codeClean")),
        cleaning_staff=_parse.json_int(detail.get("kaptdClcnt")),
        disinfection_type=_parse.json_str(detail.get("codeDisinf")),
        disinfection_staff=_parse.json_int(detail.get("kaptdDcnt")),
        disinfection_method=_parse.json_str(detail.get("disposalType")),
        garbage_type=_parse.json_str(detail.get("codeGarbage")),
        water_supply=_parse.json_str(detail.get("codeWsupply")),
        electricity_contract=_parse.json_str(detail.get("codeEcon")),
        fire_alarm=_parse.json_str(detail.get("codeFalarm")),
        internet=_parse.json_str(detail.get("codeNet")),
        elevator_count=_parse.json_int(detail.get("kaptdEcnt")),
        cctv_count=_parse.json_int(detail.get("kaptdCccnt")),
        subway_line=_parse.json_str(detail.get("subwayLine")),
        subway_station=_parse.json_str(detail.get("subwayStation")),
        subway_time=_parse.json_str(detail.get("kaptdWtimesub")),
        bus_time=_parse.json_str(detail.get("kaptdWtimebus")),
        convenient_facility_raw=_parse.json_str(detail.get("convenientFacility")),
        education_facility_raw=_parse.json_str(detail.get("educationFacility")),
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

    def fetch_page(page: int) -> tuple[list[ComplexRef], int]:
        params["pageNo"] = page
        return _parse_list_page(fetch_text(url, params, client=client, timeout=timeout))

    return paginate(fetch_page, num_of_rows=num_of_rows)


def fetch_complex_info(
    kapt_code: str,
    *,
    api_key: str,
    client: httpx.Client | None = None,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
) -> ComplexInfo | None:
    """단지코드 → 기본정보. basis·detail 두 엔드포인트를 호출해 병합. 단지 없으면 None."""
    params = {"serviceKey": api_key, "kaptCode": kapt_code}
    basis = fetch_text(BASIS_URL, params, client=client, timeout=timeout)
    detail = fetch_text(DETAIL_URL, params, client=client, timeout=timeout)
    return parse_complex_info(basis, detail)
