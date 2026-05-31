"""서울 25구(→전국 확장 가능) 다지역 적재 배치 — C3 `run_ingest`를 지역 루프로 감싼다.

새 적재 로직 없음: 각 구를 canonical 스테이지(complex→transaction→join→geocode)로
`run_ingest`에 위임하고, **하나의 Throttle을 전 지역에 공유**(구 경계에서도 호출 간격 유지 →
개발계정 일일한도·Kakao rate 보호). 전 스테이지 멱등이라 재실행=재개(중단 안전).

전국 확장: REGIONS에 시군구 코드를 추가하면 같은 명령으로 넓어진다(geocode는 여러 run 분산).

    uv run python scripts/ingest_seoul.py                       # 25구·최근 12개월·전 스테이지
    uv run python scripts/ingest_seoul.py --regions 11110,11140 # 일부 구
    uv run python scripts/ingest_seoul.py --stages complex --months 202504-202604
"""

from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import _bootstrap  # noqa: F401  (side-effect: apps/api를 sys.path에 — PYTHONPATH 불필요)

from app.ingest import STAGE_ORDER, IngestSummary, parse_months, run_ingest
from app.settings import get_api_key, get_kakao_key
from app.store.db import DEFAULT_DB_PATH, get_connection, init_db
from app.throttle import Throttle

# 서울 25구 시군구코드(법정동 앞 5자리). 전국 확장 시 여기에 코드 추가.
SEOUL_25: list[tuple[str, str]] = [
    ("11110", "종로구"), ("11140", "중구"), ("11170", "용산구"), ("11200", "성동구"),
    ("11215", "광진구"), ("11230", "동대문구"), ("11260", "중랑구"), ("11290", "성북구"),
    ("11305", "강북구"), ("11320", "도봉구"), ("11350", "노원구"), ("11380", "은평구"),
    ("11410", "서대문구"), ("11440", "마포구"), ("11470", "양천구"), ("11500", "강서구"),
    ("11530", "구로구"), ("11545", "금천구"), ("11560", "영등포구"), ("11590", "동작구"),
    ("11620", "관악구"), ("11650", "서초구"), ("11680", "강남구"), ("11710", "송파구"),
    ("11740", "강동구"),
]


@dataclass
class RegionResult:
    code: str
    name: str
    summary: IngestSummary


def recent_months(today: date, n: int = 12) -> list[str]:
    """today 기준 직전 n개월 YYYYMM(마지막 완료월부터 역순으로 n개, 오름차순 반환)."""
    year, month = today.year, today.month
    # 마지막 완료월 = 전월
    month -= 1
    if month == 0:
        month = 12
        year -= 1
    months: list[str] = []
    for _ in range(n):
        months.append(f"{year:04d}{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return sorted(months)


def run_batch(
    conn: sqlite3.Connection,
    regions: list[tuple[str, str]],
    *,
    months: list[str],
    stages: list[str],
    api_key: str | None,
    kakao_key: str | None,
    throttle: Throttle | None = None,
    log: Callable[[str], None] = print,
) -> list[RegionResult]:
    """각 구를 run_ingest에 위임(공유 throttle). 구별 요약 수집·반환. 멱등(재실행=재개)."""
    results: list[RegionResult] = []
    for i, (code, name) in enumerate(regions, 1):
        log(f"━━ [{i}/{len(regions)}] {name}({code}) 적재 시작 ━━")
        summary = run_ingest(
            conn, region=code, months=months, stages=stages,
            api_key=api_key, kakao_key=kakao_key, throttle=throttle, log=log,
        )
        results.append(RegionResult(code=code, name=name, summary=summary))
    return results


def coverage_table(results: list[RegionResult]) -> str:
    """구별 커버리지 표 + 서울 합계(단지·거래·geocode율)."""
    lines = ["", "구별 커버리지", "code   name        단지   거래    geocode"]
    tc = tt = tg = tgt = 0
    for r in results:
        s = r.summary
        geo = f"{s.geocoded}/{s.geocode_total}" if s.geocode_total else "—"
        lines.append(f"{r.code}  {r.name:9s}  {s.complexes:5d}  {s.transactions:6d}  {geo}")
        tc += s.complexes
        tt += s.transactions
        tg += s.geocoded
        tgt += s.geocode_total
    geo_pct = f"{tg}/{tgt} ({100 * tg // tgt}%)" if tgt else "—"
    lines.append(f"합계: 단지 {tc} · 거래 {tt} · geocode {geo_pct}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ingest_seoul", description="서울 다지역 적재 배치")
    parser.add_argument("--regions", default="all", help="all 또는 콤마구분 시군구코드")
    parser.add_argument("--months", default="", help="YYYYMM 범위/목록(빈값=최근 12개월)")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite 경로")
    parser.add_argument("--stages", default="all", help="all 또는 스테이지 부분선택(콤마)")
    parser.add_argument("--interval", type=float, default=0.2, help="API 호출 간 최소 간격(초)")
    args = parser.parse_args(argv)

    if args.regions == "all":
        regions = list(SEOUL_25)
    else:
        wanted = {c.strip() for c in args.regions.split(",") if c.strip()}
        regions = [(c, n) for c, n in SEOUL_25 if c in wanted]
        unknown = wanted - {c for c, _ in SEOUL_25}
        if unknown:
            parser.error(f"서울 25구에 없는 코드: {sorted(unknown)}")

    stages = (
        list(STAGE_ORDER)
        if args.stages == "all"
        else [s.strip() for s in args.stages.split(",") if s.strip()]
    )
    unknown_stages = [s for s in stages if s not in STAGE_ORDER]
    if unknown_stages:
        parser.error(f"알 수 없는 stage: {unknown_stages} (가능: {STAGE_ORDER})")

    months = parse_months(args.months) if args.months else recent_months(date.today())

    conn = get_connection(args.db)
    init_db(conn)
    api_key = get_api_key() if ({"complex", "transaction"} & set(stages)) else None
    kakao_key = get_kakao_key() if "geocode" in stages else None

    results = run_batch(
        conn, regions, months=months, stages=stages,
        api_key=api_key, kakao_key=kakao_key, throttle=Throttle(args.interval),
    )
    print(coverage_table(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
