"""시군구코드 → 행정구역명 룩업(geocode 동명 중복 해소용).

비-아파트(RH/Offi) 전월세는 도로명(roadNm)이 없어 geocode를 '법정동 지번'으로 한다. 그런데
'중구 영주동'처럼 **동/구 이름이 시·도를 가로질러 중복**되면 Kakao가 엉뚱한 도시로 오지오코딩한다
(부산 중구 영주동 → 경북 영주시). 시군구코드(5자리)로 '시도 시군구'를 앞에 붙여 해소한다.
출처: data/regions/sigungu_kr.csv(code,sido,sigungu) — 전국 적재 코드표와 동일.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

# app/store/regions.py → parents[2] = apps/api (db.py의 DEFAULT_DB_PATH과 동일 기준)
_REGIONS_CSV = Path(__file__).resolve().parents[2] / "data" / "regions" / "sigungu_kr.csv"
# enrich-1: (sgg_cd, 법정동명) → bjdongCd(5자리) — 건축물대장 조회 키. Kakao b_code로 일회 생성
# (scripts/gen_bjdong_ref.py)된 정적 참조. 런타임 키 불필요(키리스 게이트 안전).
_BJDONG_CSV = Path(__file__).resolve().parents[2] / "data" / "regions" / "bjdong_kr.csv"


@lru_cache(maxsize=1)
def _sgg_map() -> dict[str, str]:
    """시군구코드 → '시도 시군구'(1회 로드·캐시). CSV 없으면 빈 맵(fallback 동작)."""
    out: dict[str, str] = {}
    if not _REGIONS_CSV.exists():
        return out
    with _REGIONS_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = (row.get("code") or "").strip()
            sido = (row.get("sido") or "").strip()
            sigungu = (row.get("sigungu") or "").strip()
            label = f"{sido} {sigungu}".strip()
            if code and label:
                out[code] = label
    return out


def sigungu_label(sgg_cd: str | None) -> str | None:
    """5자리 시군구코드 → '시도 시군구'(예: '11680'→'서울특별시 강남구'). 미매핑/None이면 None."""
    if not sgg_cd:
        return None
    return _sgg_map().get(sgg_cd.strip())


@lru_cache(maxsize=1)
def _bjdong_map() -> dict[tuple[str, str], str]:
    """(sgg_cd, 법정동명) → bjdongCd(5자리)(1회 로드·캐시). CSV 없으면 빈 맵(미enrich)."""
    out: dict[tuple[str, str], str] = {}
    if not _BJDONG_CSV.exists():
        return out
    with _BJDONG_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sgg = (row.get("sgg_cd") or "").strip()
            dong = (row.get("legal_dong") or "").strip()
            bjd = (row.get("bjdong_cd") or "").strip()
            if sgg and dong and bjd:
                out[(sgg, dong)] = bjd
    return out


def bjdong_code(sgg_cd: str | None, legal_dong: str | None) -> str | None:
    """(시군구코드, 법정동명) → bjdongCd(5자리). 건축물대장 조회 키. 미매핑/None이면 None."""
    if not sgg_cd or not legal_dong:
        return None
    return _bjdong_map().get((sgg_cd.strip(), legal_dong.strip()))
