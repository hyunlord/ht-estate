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
from app.store.join_repo import backfill_matches, backfill_rent_bjd
from app.store.progress_repo import completed_months, record_month, region_has_complex
from app.store.rent_transaction_repo import ingest_rent_month, ingest_rent_months
from app.store.transaction_repo import ingest_month, ingest_months
from app.throttle import Throttle

STAGE_ORDER = ["complex", "transaction", "rent", "join", "geocode"]
# `--stages all`의 기본 확장 — 전월세(rent)는 별도 활용신청 필요라 **opt-in**(명시 선택)으로 둔다.
# (기존 ops `--stages all` 동작 불변 = 매매 회귀 0). rent는 `--stages rent` 등으로 명시 선택.
DEFAULT_STAGES = ["complex", "transaction", "join", "geocode"]
# 각 단계의 선행(같은 run에 함께 없으면 경고 — 이전 run에서 적재됐을 수 있어 막진 않음).
_PREREQS = {"join": ("complex", "transaction"), "geocode": ("complex",), "rent": ("complex",)}
GEO_SOURCE = "Kakao Local 주소검색"


@dataclass
class IngestSummary:
    complexes: int = 0
    transactions: int = 0
    matched: int = 0
    join_total: int = 0
    geocoded: int = 0
    geocode_total: int = 0
    # P2-1 전월세 (별도 축 — 매매 필드 불변)
    rent_transactions: int = 0
    rent_matched: int = 0
    rent_join_total: int = 0

    def __str__(self) -> str:
        join_pct = f"{self.matched}/{self.join_total}" if self.join_total else "—"
        geo_pct = f"{self.geocoded}/{self.geocode_total}" if self.geocode_total else "—"
        base = (
            f"적재 요약 — 단지 {self.complexes} · 거래 {self.transactions} · "
            f"조인 matched {join_pct} · 지오코딩 {geo_pct}"
        )
        if self.rent_transactions or self.rent_join_total:
            rp = f"{self.rent_matched}/{self.rent_join_total}" if self.rent_join_total else "—"
            base += f" · 전월세 {self.rent_transactions}(조인 {rp})"
        return base


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


def _ingest_months_resumable(
    conn: sqlite3.Connection,
    stage: str,
    region: str,
    months: list[str],
    fetch_one: Callable[[str], int],
    *,
    throttle: Throttle | None,
    log: Callable[[str], None],
) -> int:
    """월 단위 재개 적재 — 원장에 기록된 월 skip, 적재 후 기록. 총 행수 반환 (C20).

    fetch_one(month)->행수 가 한 region×월을 적재(멱등). 월마다 fetch 후 원장에 기록하므로
    중단돼도 완료 월은 재fetch 안 함(최악: 진행 중이던 1개월 재fetch — 멱등이라 안전).
    일일캡(1,000/day) 멀티데이 재개의 핵심: 이미 한 월에 캡을 다시 쓰지 않는다.
    """
    done = completed_months(conn, stage, region)
    pending = [m for m in months if m not in done]
    skipped = len(months) - len(pending)
    if skipped:
        log(f"  ↻ {stage} {region}: {skipped}개월 skip → {len(pending)}개월 적재")
    total = 0
    for month in pending:
        if throttle is not None:
            throttle.wait()
        rows = fetch_one(month)
        record_month(conn, stage, region, month, rows)
        total += rows
    return total


def run_ingest(
    conn: sqlite3.Connection,
    *,
    region: str,
    months: list[str],
    stages: list[str],
    api_key: str | None = None,
    kakao_key: str | None = None,
    throttle: Throttle | None = None,
    resume: bool = False,
    log: Callable[[str], None] = print,
) -> IngestSummary:
    """선택 단계를 canonical 순서로 실행. 선행 미선택시 경고(막진 않음). 요약 반환.

    `resume=True`면 멀티데이 재개: complex는 region 기적재 시 skip, transaction/rent는
    원장(ingest_progress)에 기록된 월을 skip(일일캡 보존). join/geocode는 본래 증분(있으면 skip)
    이라 resume 무관. 기본(resume=False)은 기존 동작 불변(매 run 재fetch — 단발 dev/refresh용).
    """
    selected = _selected(stages)
    for stage in selected:
        missing = [p for p in _PREREQS.get(stage, ()) if p not in selected]
        if missing:
            log(f"⚠️ '{stage}' 단계 선행 미선택: {missing} (이전 run 데이터 가정하고 진행)")

    summary = IngestSummary()
    if "complex" in selected:
        if resume and region_has_complex(conn, region):
            log(f"  ↻ complex {region}: 기적재 skip")
        else:
            assert api_key is not None
            summary.complexes = ingest_complexes(
                conn, region=region, api_key=api_key, throttle=throttle, log=log
            )
    if "transaction" in selected:
        assert api_key is not None
        if resume:
            summary.transactions = _ingest_months_resumable(
                conn, "transaction", region, months,
                lambda m: ingest_month(conn, region, m, api_key=api_key),
                throttle=throttle, log=log,
            )
        else:
            summary.transactions = ingest_months(
                conn, region, months, api_key=api_key, throttle=throttle
            )
    if "rent" in selected:
        assert api_key is not None
        if resume:
            summary.rent_transactions = _ingest_months_resumable(
                conn, "rent", region, months,
                lambda m: ingest_rent_month(conn, region, m, api_key=api_key),
                throttle=throttle, log=log,
            )
        else:
            summary.rent_transactions = ingest_rent_months(
                conn, region, months, api_key=api_key, throttle=throttle
            )
        # 전월세는 umdCd 없어 bjd_code NULL → (sgg,동명)→bjd 룩업으로 채워 bjd narrowing 회복(P2-3).
        backfill_rent_bjd(conn)
        # 전월세 조인은 rent 스테이지 내에서(동형 조인 재사용). 매매 "join" 스테이지는 불변(회귀 0).
        rent_stats = backfill_matches(conn, table="rent_transaction")
        summary.rent_matched = rent_stats["matched"]
        summary.rent_join_total = rent_stats["total"]
    if "join" in selected:
        stats = backfill_matches(conn)  # 매매 — 동작 불변
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
        "--stages", default="all",
        help="all(=complex,transaction,join,geocode) 또는 부분선택. rent는 opt-in(명시 필요)",
    )
    parser.add_argument(
        "--interval", type=float, default=0.2, help="외부 API 호출 간 최소 간격(초)"
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="멀티데이 재개: complex 기적재 region·transaction/rent 기적재 월 skip(일일캡 보존)",
    )
    args = parser.parse_args(argv)

    stages = (
        list(DEFAULT_STAGES)  # all = 매매 4단계(rent opt-in)
        if args.stages == "all"
        else [s.strip() for s in args.stages.split(",") if s.strip()]
    )
    unknown = [s for s in stages if s not in STAGE_ORDER]
    if unknown:
        parser.error(f"알 수 없는 stage: {unknown} (가능: {STAGE_ORDER})")

    conn = get_connection(args.db)
    init_db(conn)
    api_key = get_api_key() if ({"complex", "transaction", "rent"} & set(stages)) else None
    kakao_key = get_kakao_key() if "geocode" in stages else None

    run_ingest(
        conn,
        region=args.region,
        months=parse_months(args.months),
        stages=stages,
        api_key=api_key,
        kakao_key=kakao_key,
        throttle=Throttle(args.interval),
        resume=args.resume,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
