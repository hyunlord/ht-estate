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
