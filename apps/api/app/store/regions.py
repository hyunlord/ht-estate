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


# region-normalize(#6-②): 시군구코드 → (sido, sigungu) 구조화 룩업 + 시도 변종 정규화.
# sigungu_kr.csv가 진실원천(CSV의 sigungu가 통합시 일반구 머지형 "용인처인구"·공백 없음).
@lru_cache(maxsize=1)
def _sgg_region_map() -> dict[str, tuple[str, str]]:
    """시군구코드 → (sido, sigungu) — CSV authoritative. 비면 빈 맵."""
    out: dict[str, tuple[str, str]] = {}
    if not _REGIONS_CSV.exists():
        return out
    with _REGIONS_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = (row.get("code") or "").strip()
            sido = (row.get("sido") or "").strip()
            sigungu = (row.get("sigungu") or "").strip()
            if code and sido:
                out[code] = (sido, sigungu)
    return out


@lru_cache(maxsize=1)
def _sido_set() -> frozenset[str]:
    """CSV canonical 시도명 집합 — canonical_sido가 '정규화 결과가 CSV에 있나' 검증에 씀."""
    return frozenset(sido for sido, _ in _sgg_region_map().values())


# 도명변경 변종 → CSV canonical(2024 특별자치 전환 등). **임의 시삽입 아님** — 명시 매핑만.
# canonical_sido는 결과가 CSV 집합에 있을 때만 반환(미매칭 변종은 None → 백필이 NULL 유지·§9 보고).
_SIDO_VARIANTS = {
    "강원": "강원특별자치도", "강원도": "강원특별자치도",
    "전북": "전북특별자치도", "전라북도": "전북특별자치도",
    "제주": "제주특별자치도", "제주도": "제주특별자치도",
    "세종": "세종특별자치시", "세종시": "세종특별자치시",
}


def region_by_code(sgg_cd: str | None) -> tuple[str, str] | None:
    """5자리 시군구코드 → (sido, sigungu) CSV canonical. 미매핑/None이면 None."""
    if not sgg_cd:
        return None
    return _sgg_region_map().get(sgg_cd.strip())


def canonical_sido(raw: str | None) -> str | None:
    """파싱한 시도 변종을 CSV canonical로. CSV에 이미 있으면 그대로, 알려진 변종이면 매핑,
    그 외(미매칭)는 None(억지 정규화 금지 — 백필이 NULL 유지하고 §9에 보고)."""
    s = (raw or "").strip()
    if not s:
        return None
    if s in _sido_set():
        return s
    mapped = _SIDO_VARIANTS.get(s)
    return mapped if mapped and mapped in _sido_set() else None


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
