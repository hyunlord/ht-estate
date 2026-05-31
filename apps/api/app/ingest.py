"""적재 오케스트레이션 러너 — 단일 명령으로 4단계 순서 적재 (설계 §5).

`python -m app.ingest --region 11680 --months 202401-202403` →
  ① 단지 적재 → ② 실거래 적재 → ③ 조인 backfill → ④ 지오코딩 backfill.

새 적재 로직 없음 — 기존 함수(ingest_complexes·ingest_months·backfill_matches·
backfill_coords) 재사용 + 순서 강제. 각 단계 멱등이라 재실행=재개(체크포인트 불요).
스테이지 함수는 모듈 레벨 참조라 테스트가 mock으로 순서·요약을 키리스 검증한다.
"""

from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from app.geo.geocoder import geocode
from app.settings import get_api_key, get_kakao_key
from app.store.complex_repo import ingest_complexes
from app.store.db import DEFAULT_DB_PATH, get_connection, init_db
from app.store.geo_repo import backfill_coords
from app.store.join_repo import backfill_matches
from app.store.transaction_repo import ingest_months
from app.throttle import Throttle

STAGE_ORDER = ["complex", "transaction", "join", "geocode"]
# 각 단계의 선행(같은 run에 함께 없으면 경고 — 이전 run에서 적재됐을 수 있어 막진 않음).
_PREREQS = {"join": ("complex", "transaction"), "geocode": ("complex",)}
GEO_SOURCE = "Kakao Local 주소검색"


@dataclass
class IngestSummary:
    complexes: int = 0
    transactions: int = 0
    matched: int = 0
    join_total: int = 0
    geocoded: int = 0
    geocode_total: int = 0

    def __str__(self) -> str:
        join_pct = f"{self.matched}/{self.join_total}" if self.join_total else "—"
        geo_pct = f"{self.geocoded}/{self.geocode_total}" if self.geocode_total else "—"
        return (
            f"적재 요약 — 단지 {self.complexes} · 거래 {self.transactions} · "
            f"조인 matched {join_pct} · 지오코딩 {geo_pct}"
        )


def parse_months(spec: str) -> list[str]:
    """'202401-202403'(범위) 또는 '202401,202403'(목록) → YYYYMM 리스트. 빈 문자열은 []."""
    spec = spec.strip()
    if not spec:
        return []
    if "-" in spec and "," not in spec:
        start, end = (part.strip() for part in spec.split("-", 1))
        return _month_range(start, end)
    return [m.strip() for m in spec.split(",") if m.strip()]


def _month_range(start: str, end: str) -> list[str]:
    year, month = int(start[:4]), int(start[4:6])
    end_year, end_month = int(end[:4]), int(end[4:6])
    months: list[str] = []
    while (year, month) <= (end_year, end_month):
        months.append(f"{year:04d}{month:02d}")
        month += 1
        if month > 12:
            month = 1
            year += 1
    return months


def _selected(stages: list[str]) -> list[str]:
    """입력 순서 무관하게 canonical 순서로 정렬한 선택 단계."""
    return [stage for stage in STAGE_ORDER if stage in stages]


def run_ingest(
    conn: sqlite3.Connection,
    *,
    region: str,
    months: list[str],
    stages: list[str],
    api_key: str | None = None,
    kakao_key: str | None = None,
    throttle: Throttle | None = None,
    log: Callable[[str], None] = print,
) -> IngestSummary:
    """선택 단계를 canonical 순서로 실행. 선행 미선택시 경고(막진 않음). 요약 반환."""
    selected = _selected(stages)
    for stage in selected:
        missing = [p for p in _PREREQS.get(stage, ()) if p not in selected]
        if missing:
            log(f"⚠️ '{stage}' 단계 선행 미선택: {missing} (이전 run 데이터 가정하고 진행)")

    summary = IngestSummary()
    if "complex" in selected:
        assert api_key is not None
        summary.complexes = ingest_complexes(
            conn, region=region, api_key=api_key, throttle=throttle, log=log
        )
    if "transaction" in selected:
        assert api_key is not None
        summary.transactions = ingest_months(
            conn, region, months, api_key=api_key, throttle=throttle
        )
    if "join" in selected:
        stats = backfill_matches(conn)
        summary.matched = stats["matched"]
        summary.join_total = stats["total"]
    if "geocode" in selected:
        assert kakao_key is not None
        stats = backfill_coords(
            conn,
            lambda addr: geocode(addr, api_key=kakao_key),
            geo_source=GEO_SOURCE,
            throttle=throttle,
        )
        summary.geocoded = stats["matched"]
        summary.geocode_total = stats["total"]

    log(str(summary))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ingest", description="ht-estate 적재 오케스트레이션 러너"
    )
    parser.add_argument("--region", required=True, help="시군구 코드 (예: 11680 강남구)")
    parser.add_argument(
        "--months", default="", help="YYYYMM 범위(202401-202403) 또는 목록(202401,202403)"
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite 경로")
    parser.add_argument(
        "--stages", default="all", help="all 또는 complex,transaction,join,geocode 부분선택"
    )
    parser.add_argument(
        "--interval", type=float, default=0.2, help="외부 API 호출 간 최소 간격(초)"
    )
    args = parser.parse_args(argv)

    stages = (
        list(STAGE_ORDER)
        if args.stages == "all"
        else [s.strip() for s in args.stages.split(",") if s.strip()]
    )
    unknown = [s for s in stages if s not in STAGE_ORDER]
    if unknown:
        parser.error(f"알 수 없는 stage: {unknown} (가능: {STAGE_ORDER})")

    conn = get_connection(args.db)
    init_db(conn)
    api_key = get_api_key() if ({"complex", "transaction"} & set(stages)) else None
    kakao_key = get_kakao_key() if "geocode" in stages else None

    run_ingest(
        conn,
        region=args.region,
        months=parse_months(args.months),
        stages=stages,
        api_key=api_key,
        kakao_key=kakao_key,
        throttle=Throttle(args.interval),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
