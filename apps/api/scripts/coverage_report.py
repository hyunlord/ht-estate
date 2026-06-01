"""전국 적재 커버리지 리포트 (C20) — 시도/시군구별 단지·매매·전월세·조인 recall·geocode.

전국 적재 진행을 가시화한다. 키 불필요(로컬 DB만 읽음). 254 시군구(코드표) 대비 진행률 +
스테이지별 채움/품질을 보여줘 "어디까지 됐나·다음 뭘 돌릴까"를 답한다.

    uv run python scripts/coverage_report.py                    # 기본 DB
    uv run python scripts/coverage_report.py --db path/to.db    # 지정 DB
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import _bootstrap  # noqa: F401  (apps/api를 sys.path에)

from app.store.db import DEFAULT_DB_PATH, get_connection, init_db

CODES_CSV = Path(__file__).resolve().parents[1] / "data" / "regions" / "sigungu_kr.csv"


@dataclass
class RegionCoverage:
    code: str
    sido: str
    sigungu: str
    complexes: int = 0
    geocoded: int = 0
    geo_total: int = 0  # road_addr 있는(=geocode 가능) 단지
    sale: int = 0
    sale_matched: int = 0
    rent: int = 0
    rent_matched: int = 0


def _group_counts(conn: sqlite3.Connection, sql: str) -> dict[str, tuple[int, int]]:
    """sgg5 → (total, matched) 집계 헬퍼. sql은 (sgg5, total, matched) 행을 낸다."""
    return {r[0]: (r[1], r[2]) for r in conn.execute(sql) if r[0]}


def collect_coverage(
    conn: sqlite3.Connection, codes: list[tuple[str, str, str]]
) -> list[RegionCoverage]:
    """코드표(254 시군구) 각각에 대해 DB 적재 현황 집계. 코드표 순서 유지."""
    cx = _group_counts(
        conn,
        "SELECT substr(bjd_code,1,5) s, COUNT(*), "
        "SUM(CASE WHEN lat IS NOT NULL THEN 1 ELSE 0 END) "
        "FROM complex WHERE bjd_code IS NOT NULL GROUP BY s",
    )
    geo_total = _group_counts(
        conn,
        "SELECT substr(bjd_code,1,5) s, COUNT(*), 0 FROM complex "
        "WHERE bjd_code IS NOT NULL AND road_addr IS NOT NULL GROUP BY s",
    )
    sale = _group_counts(
        conn,
        'SELECT substr(bjd_code,1,5) s, COUNT(*), '
        "SUM(CASE WHEN complex_id IS NOT NULL THEN 1 ELSE 0 END) "
        'FROM "transaction" WHERE bjd_code IS NOT NULL GROUP BY s',
    )
    rent = _group_counts(
        conn,
        "SELECT COALESCE(sgg_cd, substr(bjd_code,1,5)) s, COUNT(*), "
        "SUM(CASE WHEN complex_id IS NOT NULL THEN 1 ELSE 0 END) "
        "FROM rent_transaction GROUP BY s",
    )
    out: list[RegionCoverage] = []
    for code, sido, sigungu in codes:
        c_total, c_geo = cx.get(code, (0, 0))
        out.append(
            RegionCoverage(
                code=code, sido=sido, sigungu=sigungu,
                complexes=c_total, geocoded=c_geo, geo_total=geo_total.get(code, (0, 0))[0],
                sale=sale.get(code, (0, 0))[0], sale_matched=sale.get(code, (0, 0))[1],
                rent=rent.get(code, (0, 0))[0], rent_matched=rent.get(code, (0, 0))[1],
            )
        )
    return out


def _pct(n: int, d: int) -> str:
    return f"{100 * n // d}%" if d else "—"


def format_coverage(rows: list[RegionCoverage]) -> str:
    """시도 rollup + 전국 진행률. 시군구 단위는 많아 시도로 집계(시군구 상세는 --verbose)."""
    total_regions = len(rows)
    loaded_regions = sum(1 for r in rows if r.complexes > 0)
    by_sido: dict[str, list[int]] = {}
    for r in rows:
        agg = by_sido.setdefault(r.sido or r.code[:2], [0, 0, 0, 0, 0, 0, 0, 0, 0])
        agg[0] += 1
        agg[1] += 1 if r.complexes else 0
        agg[2] += r.complexes
        agg[3] += r.sale
        agg[4] += r.sale_matched
        agg[5] += r.rent
        agg[6] += r.rent_matched
        agg[7] += r.geocoded
        agg[8] += r.geo_total
    lines = [
        "전국 적재 커버리지 — 시도별",
        f"시군구 적재: {loaded_regions}/{total_regions} ({_pct(loaded_regions, total_regions)})",
        "",
        f"{'시도':14s} {'시군구':>7s} {'단지':>7s} {'매매(recall)':>16s} "
        f"{'전월세(recall)':>16s} {'geocode':>9s}",
    ]
    t = [0] * 9
    for sido in sorted(by_sido):
        a = by_sido[sido]
        for i in range(9):
            t[i] += a[i]
        sale_r = f"{a[3]}({_pct(a[4], a[3])})"
        rent_r = f"{a[5]}({_pct(a[6], a[5])})"
        geo_r = f"{a[7]}/{a[8]}"
        lines.append(
            f"{sido:14s} {a[1]:>3d}/{a[0]:<3d} {a[2]:>7d} {sale_r:>16s} {rent_r:>16s} {geo_r:>9s}"
        )
    lines.append("")
    lines.append(
        f"{'합계':14s} {t[1]:>3d}/{t[0]:<3d} {t[2]:>7d} "
        f"{f'{t[3]}({_pct(t[4], t[3])})':>16s} {f'{t[5]}({_pct(t[6], t[5])})':>16s} "
        f"{f'{t[7]}/{t[8]}':>9s}"
    )
    return "\n".join(lines)


def _load_codes(path: Path) -> list[tuple[str, str, str]]:
    import csv

    rows: list[tuple[str, str, str]] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        rows.extend((r[0], r[1], r[2]) for r in reader if len(r) >= 3)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="coverage_report", description="전국 적재 커버리지")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite 경로")
    parser.add_argument("--codes-file", default=str(CODES_CSV), help="시군구 코드 CSV")
    args = parser.parse_args(argv)

    conn = get_connection(args.db)
    init_db(conn)
    codes = _load_codes(Path(args.codes_file))
    rows = collect_coverage(conn, codes)
    print(format_coverage(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
