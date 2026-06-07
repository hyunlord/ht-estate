"""건축물대장 클라이언트 — 국토부 건축HUB `BldRgstHubService`. (enrich-1)

비-아파트(연립·오피스텔) 건물의 빈 속성(구조·주용도·층수·세대/호·승강기·연면적·건폐/용적률·
높이·사용승인일)을 채우는 **벌크-eager** 소스. 라이브 probe로 확정(추정 아님):
- 엔드포인트: `apis.data.go.kr/1613000/BldRgstHubService`(BldRgstService_v2는 HTTP 500 폐기).
- `getBrTitleInfo`(표제부, 동별) — 구조·층수·승강기·연면적 등 동단위 상세. **enrich 주력.**
- `getBrRecapTitleInfo`(총괄표제부, 집합건물 1건) — 동수·총주차(totPkngCnt)·총세대.
- 조회키: sigunguCd(=sgg_cd)·bjdongCd(법정동5자리, regions.bjdong_code)·platGbCd(0=대지)·
  bun/ji(지번 본번·부번 0패딩 4자리). 키는 기존 MOLIT/K-apt 공유(resultCode 00).

`_http`/`_parse`는 MOLIT/K-apt와 공유. 적재(enrich-only·좌표보존)는 store/ledger_repo.
"""

from __future__ import annotations

from xml.etree.ElementTree import Element, fromstring

import httpx
from pydantic import BaseModel

from . import _parse
from ._http import DEFAULT_TIMEOUT, ensure_success, fetch_text

BASE_URL = "https://apis.data.go.kr/1613000/BldRgstHubService"
TITLE_OP = "getBrTitleInfo"
RECAP_OP = "getBrRecapTitleInfo"
DEFAULT_NUM_OF_ROWS = 100


def _pos_float(item: Element, tag: str) -> float | None:
    """연속량(연면적·건폐/용적률·높이) — 0/공백은 '미기록'으로 보고 None(0을 사실로 박지 않음)."""
    raw = _parse.text(item, tag)
    if not raw:
        return None
    try:
        val = float(raw)
    except ValueError:
        return None
    return val if val > 0 else None


def _pos_int(item: Element, tag: str) -> int | None:
    """양수 카운트(지상층수 등) — 0/공백은 미기록 None. (지하층수·승강기는 0이 유효 → 별도 처리)."""
    val = _parse.opt_int(item, tag)
    return val if val and val > 0 else None


def _nonneg_int(item: Element, tag: str) -> int | None:
    """0이 유효한 카운트(지하층수·승강기·호수) — 공백만 None, 0은 보존."""
    return _parse.opt_int(item, tag)


class BuildingLedgerTitle(BaseModel):
    """표제부(동별) 1건 — 비-아파트 enrich에 쓰는 객관 필드만. 다중 동은 bld_nm으로 디스앰비그."""

    bld_nm: str | None  # bldNm (건물명) — 매칭 키
    dong_nm: str | None  # dongNm (동명)
    plat_plc: str | None  # platPlc (지번주소) — 검증용
    structure: str | None  # strctCdNm (구조)
    main_purpose: str | None  # mainPurpsCdNm (주용도)
    household_count: int | None  # hhldCnt (세대수)
    ho_count: int | None  # hoCnt (호수)
    ground_floor_count: int | None  # grndFlrCnt (지상 층수)
    basement_floor_count: int | None  # ugrndFlrCnt (지하 층수)
    elevator_count: int | None  # rideUseElvtCnt + emgenUseElvtCnt (승강기)
    total_floor_area: float | None  # totArea (연면적 ㎡)
    building_coverage_ratio: float | None  # bcRat (건폐율 %)
    floor_area_ratio: float | None  # vlRat (용적률 %)
    building_height: float | None  # heit (높이 m)
    approval_date: str | None  # useAprDay (사용승인일) → ISO
    ledger_pk: str | None  # mgmBldrgstPk (대장 관리번호) — provenance


def _parse_title_item(item: Element) -> BuildingLedgerTitle:
    ride = _parse.opt_int(item, "rideUseElvtCnt") or 0
    emgen = _parse.opt_int(item, "emgenUseElvtCnt") or 0
    has_elev_tags = (_parse.text(item, "rideUseElvtCnt") or _parse.text(item, "emgenUseElvtCnt"))
    apr = _parse.yyyymmdd_to_date(_parse.text(item, "useAprDay"))
    return BuildingLedgerTitle(
        bld_nm=_parse.text(item, "bldNm"),
        dong_nm=_parse.text(item, "dongNm"),
        plat_plc=_parse.text(item, "platPlc"),
        structure=_parse.text(item, "strctCdNm"),
        main_purpose=_parse.text(item, "mainPurpsCdNm"),
        household_count=_nonneg_int(item, "hhldCnt"),
        ho_count=_nonneg_int(item, "hoCnt"),
        ground_floor_count=_pos_int(item, "grndFlrCnt"),
        basement_floor_count=_nonneg_int(item, "ugrndFlrCnt"),
        elevator_count=(ride + emgen) if has_elev_tags else None,
        total_floor_area=_pos_float(item, "totArea"),
        building_coverage_ratio=_pos_float(item, "bcRat"),
        floor_area_ratio=_pos_float(item, "vlRat"),
        building_height=_pos_float(item, "heit"),
        approval_date=apr.isoformat() if apr else None,
        ledger_pk=_parse.text(item, "mgmBldrgstPk"),
    )


def parse_title_info(xml_text: str) -> list[BuildingLedgerTitle]:
    """표제부 XML → 동별 레코드. resultCode 비성공이면 raise, malformed item만 skip(graceful)."""
    root = fromstring(xml_text)
    ensure_success(root)
    out: list[BuildingLedgerTitle] = []
    for el in root.findall(".//item"):
        try:
            out.append(_parse_title_item(el))
        except (ValueError, TypeError):
            continue
    return out


def fetch_title_info(
    sigungu_cd: str,
    bjdong_cd: str,
    bun: str,
    ji: str,
    *,
    api_key: str,
    plat_gb_cd: str = "0",
    num_of_rows: int = DEFAULT_NUM_OF_ROWS,
    client: httpx.Client | None = None,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
) -> list[BuildingLedgerTitle]:
    """한 지번(sigungu·bjdong·bun·ji)의 표제부(동별) 조회. 동 0건이면 빈 리스트."""
    params = {
        "serviceKey": api_key,
        "sigunguCd": sigungu_cd,
        "bjdongCd": bjdong_cd,
        "platGbCd": plat_gb_cd,
        "bun": bun,
        "ji": ji,
        "numOfRows": num_of_rows,
        "pageNo": 1,
        "_type": "xml",
    }
    xml_text = fetch_text(f"{BASE_URL}/{TITLE_OP}", params, client=client, timeout=timeout)
    return parse_title_info(xml_text)


def to_bun_ji(jibun_canonical: str | None) -> tuple[str, str] | None:
    """정규화 지번('489' | '489-1') → (bun, ji) 0패딩 4자리('0489','0001'). 무효면 None.

    nonapt building_key의 jibun은 to_canonical 산출(산 번지는 None이라 애초 제외됨).
    """
    if not jibun_canonical or jibun_canonical == "?":
        return None
    head, _, tail = jibun_canonical.partition("-")
    try:
        bonbun = int(head)
    except ValueError:
        return None
    bubun = 0
    if tail:
        try:
            bubun = int(tail)
        except ValueError:
            bubun = 0
    if bonbun <= 0:
        return None
    return f"{bonbun:04d}", f"{bubun:04d}"
