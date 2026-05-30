"""행안부 위치정보요약DB 로드 → {addr_key: (lat, lng)} 인덱스.

소스: 행정안전부_도로명주소 위치정보 요약DB (공공데이터포털 15050410 / juso.go.kr).
파이프(|) 구분 텍스트, 시도별 파일. 주출입구 좌표는 EPSG:5179 → WGS84로 변환 적재.
저장 가능(도로명주소법 시행령 §46 — 자체 구축/저장 허용). 원본은 레포 비커밋.

⚠️ 컬럼 위치(_COL)는 **문서화 포맷 기준 가정**이며, 실 DB 입수 시 공식 매뉴얼로
확정한다(설계 D — 라이브 포맷 검증 deferral). 위치만 바꾸면 되도록 상수로 분리.
"""

from __future__ import annotations

from collections.abc import Iterable

from .address import addr_key
from .convert import to_wgs84

# 위치정보요약DB 주출입구 레코드의 컬럼 인덱스(0-base) — 실 DB 매뉴얼로 확정 대상.
_COL = {
    "sido": 2,  # 시도명
    "sigungu": 3,  # 시군구명
    "road": 6,  # 도로명
    "underground": 7,  # 지하여부 (0=지상)
    "bonbun": 8,  # 건물본번
    "bubun": 9,  # 건물부번
    "x": 12,  # 주출입구 X (EPSG:5179)
    "y": 13,  # 주출입구 Y (EPSG:5179)
}
_MIN_COLS = max(_COL.values()) + 1


def _parse_line(line: str) -> tuple[str, tuple[float, float]] | None:
    parts = line.rstrip("\n").split("|")
    if len(parts) < _MIN_COLS:
        return None
    try:
        bonbun = int(parts[_COL["bonbun"]] or 0)
        bubun = int(parts[_COL["bubun"]] or 0)
        x = float(parts[_COL["x"]])
        y = float(parts[_COL["y"]])
    except ValueError:
        return None
    key = addr_key(parts[_COL["sido"]], parts[_COL["sigungu"]], parts[_COL["road"]], bonbun, bubun)
    return key, to_wgs84(x, y)


def load_coord_db(lines: Iterable[str]) -> dict[str, tuple[float, float]]:
    """위치정보요약DB 라인들 → {addr_key: (lat, lng)}. 깨진 라인은 skip(graceful).

    파일은 시도별로 크므로 호출부가 파일 핸들(라인 이터레이터)을 넘긴다 — 스트리밍.
    같은 키 중복 시 마지막이 이긴다(주출입구 단일 가정).
    """
    index: dict[str, tuple[float, float]] = {}
    for line in lines:
        parsed = _parse_line(line)
        if parsed is not None:
            index[parsed[0]] = parsed[1]
    return index
