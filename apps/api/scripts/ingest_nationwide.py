"""전국 적재 — C9 서울 헬퍼(run_batch·공유 throttle·재개)를 전국 시군구로 확장.

시군구 코드는 **하드코딩하지 않고 K-apt에서 도출**한다(getTotalAptList의 bjdCode 앞 5자리 =
시군구코드 → 항상 최신·날조 0). 도출 결과를 `data/regions/sigungu_kr.csv`로 커밋(리뷰·재현용).
전국은 data.go.kr 일일한도·Kakao rate로 multi-run/multi-day일 수 있어 **재개로 누적**한다
(전 스테이지 멱등 + `--resume`로 이미 complex 적재된 시군구 skip). 한 run 완료가 목표 아님.

    uv run python scripts/ingest_nationwide.py --discover            # 코드 도출 → CSV 갱신(키 필요)
    uv run python scripts/ingest_nationwide.py --stages complex --resume   # 미적재 시군구만 complex
    uv run python scripts/ingest_nationwide.py --resume              # 전 스테이지(재개)
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from collections.abc import Callable
from datetime import date
from pathlib import Path

import _bootstrap  # noqa: F401  (side-effect: apps/api를 sys.path에 — PYTHONPATH 불필요)
import httpx
from ingest_seoul import RegionResult, coverage_table, recent_months, run_batch

from app.ingest import API_KEY_STAGES, DEFAULT_STAGES, STAGE_ORDER, parse_months
from app.settings import get_api_key, get_kakao_key
from app.sources.kapt import list_complexes
from app.store.db import DEFAULT_DB_PATH, get_connection, init_db
from app.store.progress_repo import completed_months, region_has_complex
from app.throttle import Throttle

# region 선택을 gating하는 스테이지(미적재 시 그 region에 할 일 있음). geocode/join은
# present-skip(있으면 skip)이라 region 선택을 막지 않는다 — 이들만 선택되면 전 region 통과.
_GATING_STAGES = ("complex", "transaction", "rent")

CODES_CSV = Path(__file__).resolve().parents[1] / "data" / "regions" / "sigungu_kr.csv"


def discover_sigungu(
    api_key: str, client: httpx.Client | None = None
) -> list[tuple[str, str, str]]:
    """K-apt 전국 단지목록 → distinct 시군구코드(bjdCode 앞5) + 대표 시도/시군구명. 코드순 정렬."""
    refs = list_complexes(api_key=api_key, client=client)  # 인자 없음 → 전국 total
    seen: dict[str, tuple[str, str]] = {}
    for ref in refs:
        if ref.bjd_code and len(ref.bjd_code) >= 5:
            code = ref.bjd_code[:5]
            if code not in seen:
                seen[code] = (ref.sido or "", ref.sigungu or "")
    return sorted((code, sido, sg) for code, (sido, sg) in seen.items())


def save_codes(path: Path, rows: list[tuple[str, str, str]]) -> None:
    """시군구 코드표 CSV 저장(code,sido,sigungu). 헤더 포함."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "sido", "sigungu"])
        w.writerows(rows)


def load_codes(path: Path) -> list[tuple[str, str, str]]:
    """CSV → [(code, sido, sigungu)] (헤더 skip)."""
    rows: list[tuple[str, str, str]] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)  # 헤더
        for row in reader:
            if len(row) >= 3:
                rows.append((row[0], row[1], row[2]))
    return rows


def loaded_sigungu(conn: sqlite3.Connection) -> set[str]:
    """이미 complex가 적재된 시군구코드 집합(bjdCode 앞5) — 재개 skip 판정용."""
    rows = conn.execute(
        "SELECT DISTINCT substr(bjd_code, 1, 5) FROM complex WHERE bjd_code IS NOT NULL"
    ).fetchall()
    return {r[0] for r in rows if r[0]}


def pending_regions(
    conn: sqlite3.Connection,
    codes: list[tuple[str, str, str]],
    stages: list[str],
    months: list[str],
) -> list[tuple[str, str, str]]:
    """재개: 선택 스테이지에 아직 할 일이 남은 시군구만 남긴다(전국 멀티데이 재개의 진행 추적).

    gating 스테이지(complex·transaction·rent) 기준 — complex 미적재거나, txn/rent에 미적재 월이
    하나라도 있으면 pending. geocode/join만 선택되면 gating 없음 → 전 region 통과(자체 skip).
    run_ingest의 스테이지별 self-skip이 region 내 부분완료(예: 5/12개월)를 다시 거른다.
    """
    gating = [s for s in _GATING_STAGES if s in stages]
    if not gating:
        return codes
    target = set(months)
    out: list[tuple[str, str, str]] = []
    for row in codes:
        code = row[0]
        needs = ("complex" in gating and not region_has_complex(conn, code)) or any(
            s in gating and not target <= completed_months(conn, s, code)
            for s in ("transaction", "rent")
        )
        if needs:
            out.append(row)
    return out


def sido_summary(results: list[RegionResult]) -> str:
    """시도별 집계(코드 앞2 그룹) — 시군구 수·단지·거래·geocode."""
    by_sido: dict[str, list[int]] = {}
    for r in results:
        sido = r.code[:2]
        agg = by_sido.setdefault(sido, [0, 0, 0, 0, 0])
        agg[0] += 1
        agg[1] += r.summary.complexes
        agg[2] += r.summary.transactions
        agg[3] += r.summary.geocoded
        agg[4] += r.summary.geocode_total
    lines = ["", "시도별 진행(코드앞2 · 시군구수 · 단지 · 거래 · geocode)"]
    for sido in sorted(by_sido):
        n, cx, tx, g, gt = by_sido[sido]
        geo = f"{g}/{gt}" if gt else "—"
        lines.append(f"{sido}  시군구 {n:2d} · 단지 {cx:5d} · 거래 {tx:6d} · geocode {geo}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ingest_nationwide", description="전국 적재(재개 가능)")
    parser.add_argument("--discover", action="store_true", help="K-apt에서 시군구코드 도출→CSV")
    parser.add_argument("--codes-file", default=str(CODES_CSV), help="시군구 코드 CSV 경로")
    parser.add_argument("--regions", default="all", help="all 또는 콤마구분 시군구코드(부분)")
    parser.add_argument("--resume", action="store_true", help="이미 complex 적재된 시군구 skip")
    parser.add_argument("--months", default="", help="YYYYMM 범위/목록(빈값=최근 12개월)")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite 경로")
    parser.add_argument("--stages", default="all", help="all 또는 스테이지 부분선택(콤마)")
    parser.add_argument("--interval", type=float, default=0.2, help="API 호출 간 최소 간격(초)")
    parser.add_argument("--limit", type=int, default=0, help="이번 run 최대 시군구 수(0=무제한)")
    args = parser.parse_args(argv)

    codes_path = Path(args.codes_file)

    if args.discover:
        rows = discover_sigungu(get_api_key())
        save_codes(codes_path, rows)
        sidos = sorted({c[:2] for c, _, _ in rows})
        print(f"시군구 코드 도출 — {len(rows)}개 · 시도 {len(sidos)}개 → {codes_path}")
        return 0

    codes = load_codes(codes_path)
    if args.regions != "all":
        wanted = {c.strip() for c in args.regions.split(",") if c.strip()}
        codes = [row for row in codes if row[0] in wanted]

    stages = (
        list(DEFAULT_STAGES)  # rent는 opt-in(명시) — all에 미포함
        if args.stages == "all"
        else [s.strip() for s in args.stages.split(",") if s.strip()]
    )
    unknown = [s for s in stages if s not in STAGE_ORDER]
    if unknown:
        parser.error(f"알 수 없는 stage: {unknown} (가능: {STAGE_ORDER})")

    months = parse_months(args.months) if args.months else recent_months(date.today())

    conn = get_connection(args.db)
    init_db(conn)

    if args.resume:
        # 스테이지별 self-skip(run_ingest)로 재개 — complex 기적재 region·txn/rent 기적재 월 skip.
        # region 통째 드롭 대신 "할 일 남은" region만 남긴다(풀스택 안전 + limit/진행 유의미).
        before = len(codes)
        codes = pending_regions(conn, codes, stages, months)
        print(f"재개: {before}개 중 {before - len(codes)}개 완료 → {len(codes)}개 남음")
    if args.limit > 0:
        codes = codes[: args.limit]

    regions = [(code, f"{sido} {sg}".strip()) for code, sido, sg in codes]
    log: Callable[[str], None] = print
    if not regions:
        log("적재할 시군구 없음(전부 완료 또는 필터 0).")
        return 0

    api_key = get_api_key() if (API_KEY_STAGES & set(stages)) else None
    kakao_key = get_kakao_key() if "geocode" in stages else None
    results = run_batch(
        conn, regions, months=months, stages=stages,
        api_key=api_key, kakao_key=kakao_key, throttle=Throttle(args.interval),
        resume=args.resume, log=log,
    )
    print(coverage_table(results))
    print(sido_summary(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
