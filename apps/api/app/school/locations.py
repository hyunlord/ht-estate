"""학교 위치 거리 tier (school-1) — 정부 좌표 표준데이터 오프라인 적재 + 인메모리 근접 계산.

소스: 전국초중등학교위치표준데이터(data.go.kr 15021148 · 학구도안내서비스/한국교육시설안전원).
**좌표 직제공**(위도·경도 WGS84 도 단위)이라 지오코딩·외부 API 불요 → 전부 로컬 결정론 계산
(poi와 달리 Kakao 0·쿼터 0·429 0). 학교셋은 작고 정적(초~6천·중~3천·고~2천)이라 grid 셀
인메모리 인덱스로 단지별 최근접/개수를 빠르게 낸다(stdlib only·신규 의존 0).
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass

# 출처(provenance) — 카드/저장 attribution.
SOURCE = "moe_school_location_15021148"
SOURCE_URL = "https://www.data.go.kr/data/15021148/standard.do"

# 학교급구분(표준데이터 값) → level 코드. 그 외(특수/기타)는 거리 tier 대상 아님(skip).
LEVELS: dict[str, str] = {"초등학교": "elem", "중학교": "mid", "고등학교": "high"}
LEVEL_LABELS: dict[str, str] = {"elem": "초등학교", "mid": "중학교", "high": "고등학교"}
LEVEL_ORDER = ("elem", "mid", "high")

# 운영상태 — 폐교·휴교·폐지는 제외(운영중만). 그 외/공백은 보수적으로 포함.
_CLOSED_MARKERS = ("폐교", "폐지", "휴교", "통폐합")

# 한국 좌표 bbox sanity — 위경도(도)인지 검증(EPSG:5179 미터값/오염행 drop).
_LAT_MIN, _LAT_MAX = 33.0, 39.5
_LNG_MIN, _LNG_MAX = 124.0, 132.0

# grid 셀 크기(도). ±2 셀(5×5)이 ~3.5km(lng)·4.4km(lat)를 덮어 1km 카운트·근접을 보장.
_CELL = 0.02
_WIN = 2

# CSV 헤더(표준데이터 항목명) — BOM/공백 관용 매핑.
_H_ID = "학교ID"
_H_NAME = "학교명"
_H_LEVEL = "학교급구분"
_H_STATUS = "운영상태"
_H_LAT = "위도"
_H_LNG = "경도"


@dataclass(frozen=True)
class School:
    school_id: str
    name: str
    level: str  # elem|mid|high
    lat: float
    lng: float


@dataclass
class NearestResult:
    """단지×level 근접 요약. 학교 0개(level 미존재)면 전부 None."""

    nearest_dist_m: int | None
    nearest_name: str | None
    nearest_school_id: str | None
    count_500m: int | None
    count_1km: int | None


def _is_operating(status: str) -> bool:
    return not any(m in status for m in _CLOSED_MARKERS)


def _to_float(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_schools(path: str) -> list[School]:
    """표준데이터 CSV → School 리스트. 운영중·초/중/고·유효 WGS84 좌표만(나머지 drop).

    인코딩 관용(utf-8-sig: data.go.kr CSV는 BOM·cp949 가능 → utf-8-sig 우선, 실패 시 cp949).
    """
    try:
        rows = _read_csv(path, "utf-8-sig")
    except UnicodeDecodeError:
        rows = _read_csv(path, "cp949")
    out: list[School] = []
    for r in rows:
        level = LEVELS.get((r.get(_H_LEVEL) or "").strip())
        if level is None:
            continue  # 특수/기타 학교급 → 거리 tier 대상 아님
        if not _is_operating((r.get(_H_STATUS) or "").strip()):
            continue  # 폐교/휴교 제외
        lat, lng = _to_float(r.get(_H_LAT, "")), _to_float(r.get(_H_LNG, ""))
        if lat is None or lng is None:
            continue
        if not (_LAT_MIN <= lat <= _LAT_MAX and _LNG_MIN <= lng <= _LNG_MAX):
            continue  # 좌표계 sanity(도 단위 WGS84 아님 → drop)
        sid = (r.get(_H_ID) or "").strip()
        name = (r.get(_H_NAME) or "").strip()
        if not sid or not name:
            continue
        out.append(School(school_id=sid, name=name, level=level, lat=lat, lng=lng))
    return out


def _read_csv(path: str, encoding: str) -> list[dict]:
    with open(path, encoding=encoding, newline="") as f:
        return list(csv.DictReader(f))


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """두 WGS84 좌표 간 대원거리(m). 거리 tier 결정론 계산."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _cell(lat: float, lng: float) -> tuple[int, int]:
    return (math.floor(lat / _CELL), math.floor(lng / _CELL))


class SchoolIndex:
    """level별 grid 셀 인덱스 — 단지 좌표 → 최근접 학교 + 500m/1km 개수(결정론·인메모리)."""

    def __init__(self, schools: list[School]) -> None:
        self._grids: dict[str, dict[tuple[int, int], list[School]]] = {}
        for s in schools:
            self._grids.setdefault(s.level, {}).setdefault(_cell(s.lat, s.lng), []).append(s)

    def levels_present(self) -> set[str]:
        return set(self._grids)

    def nearest(self, level: str, lat: float, lng: float) -> NearestResult:
        grid = self._grids.get(level)
        if not grid:
            return NearestResult(None, None, None, None, None)  # 해당 level 학교 0개
        c0 = _cell(lat, lng)
        window: list[School] = []
        for dr in range(-_WIN, _WIN + 1):
            for dc in range(-_WIN, _WIN + 1):
                window.extend(grid.get((c0[0] + dr, c0[1] + dc), ()))
        if window:
            dists = [(haversine_m(lat, lng, s.lat, s.lng), s) for s in window]
            best_d, best_s = min(dists, key=lambda t: t[0])
            return NearestResult(
                nearest_dist_m=round(best_d),
                nearest_name=best_s.name,
                nearest_school_id=best_s.school_id,
                count_500m=sum(1 for d, _ in dists if d <= 500),
                count_1km=sum(1 for d, _ in dists if d <= 1000),
            )
        # rural — ±2 윈도(~3.5km)에 학교 0 → 셸 확장으로 최근접만(개수는 1km 내 0 자명)
        for ring in range(_WIN + 1, 60):
            shell: list[School] = []
            for dr in range(-ring, ring + 1):
                for dc in range(-ring, ring + 1):
                    if max(abs(dr), abs(dc)) != ring:
                        continue
                    shell.extend(grid.get((c0[0] + dr, c0[1] + dc), ()))
            if shell:
                best_d, best_s = min(
                    ((haversine_m(lat, lng, s.lat, s.lng), s) for s in shell), key=lambda t: t[0]
                )
                return NearestResult(round(best_d), best_s.name, best_s.school_id, 0, 0)
        return NearestResult(None, None, None, 0, 0)
