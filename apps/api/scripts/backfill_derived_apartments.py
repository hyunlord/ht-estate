"""거래-도출 아파트 건물 백필 (#6-③B-2·gated) — orphan 아파트 거래를 미등재 건물로.

post-join complex_id NULL 아파트 거래(매매+전월세) 중 **jibun/bjd 있는 것만** building_key로
도출해 thin complex 행 upsert + txn 링크(match_confidence=1.0·멱등). 도출불가(jibun/bjd 결손)는
NULL 유지(억지생성 0·천장 수용). fuzzy 매칭된 txn(NOT NULL)은 무접촉.

**gated**: --dry-run(기본)은 카운트만(write 0). 실제 write는 --apply에서만(B-2b). 배치 단위
ShlockBatch로 거래 cron과 직렬화(굶김 방지)·--limit per-run·resume(NULL만 처리라 재실행 안전).

    uv run python scripts/backfill_derived_apartments.py              # dry-run(사이징만)
    uv run python scripts/backfill_derived_apartments.py --apply --limit 5000   # B-2b
"""

from __future__ import annotations

import argparse
import sqlite3

import _bootstrap  # noqa: F401  (apps/api를 sys.path에)
from refill_kapt_fields import ShlockBatch

from app.store.db import DEFAULT_DB_PATH, get_connection
from app.store.join_repo import JOINABLE_TABLES
from app.store.nonapt_repo import (
    DerivedAptTrade,
    building_key,
    is_derivable_apt,
    upsert_apartment_building,
)

_LOCK_PATH = str(DEFAULT_DB_PATH.parent / ".ingest.lock")  # 거래 cron과 동일 공유 락
TABLES = ("transaction", "rent_transaction")


def _flush(conn: sqlite3.Connection, table: str, batch: list, lock: ShlockBatch) -> int:
    """배치를 공유 락 점유 하에 write(건물 upsert + txn 링크). 락 미획득이면 0(다음 run resume)."""
    with lock() as acquired:
        if not acquired:
            return 0
        for txn_id, trade, key in batch:
            upsert_apartment_building(conn, trade)
            # complex_id IS NULL 가드 — fuzzy 매칭분 무접촉·멱등(재실행 시 이미 채운 행 skip).
            conn.execute(
                f'UPDATE "{table}" SET complex_id = ?, match_confidence = 1.0 '
                "WHERE txn_id = ? AND complex_id IS NULL",
                (key, txn_id),
            )
        conn.commit()
        return len(batch)


def run_backfill(
    conn: sqlite3.Connection, *, table: str, apply: bool, limit: int, batch_size: int,
    lock: ShlockBatch, log=print,
) -> dict[str, int]:
    """{derivable, nonderivable, buildings, linked} 반환. apply=False면 카운트만(write 0)."""
    if table not in JOINABLE_TABLES:
        raise ValueError(f"조인 불가 테이블: {table}")
    pending = conn.execute(
        f'SELECT txn_id, apt_name_raw, bjd_code, legal_dong, jibun, build_year FROM "{table}" '
        "WHERE complex_id IS NULL"
    ).fetchall()
    buildings: set[str] = set()
    derivable = nonderiv = linked = 0
    batch: list = []
    for row in pending:
        if not is_derivable_apt(row):
            nonderiv += 1
            continue
        trade = DerivedAptTrade.from_txn_row(row)
        key = building_key(trade)
        derivable += 1
        buildings.add(key)
        if apply:
            batch.append((row["txn_id"], trade, key))
            if len(batch) >= batch_size:
                linked += _flush(conn, table, batch, lock)
                batch = []
                if limit and linked >= limit:
                    log(f"  [limit] {table} {linked} 링크 — 이번 run 종료(다음 resume)")
                    return {"derivable": derivable, "nonderivable": nonderiv,
                            "buildings": len(buildings), "linked": linked}
    if apply and batch:
        linked += _flush(conn, table, batch, lock)
    return {"derivable": derivable, "nonderivable": nonderiv,
            "buildings": len(buildings), "linked": linked}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backfill_derived_apartments")
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--apply", action="store_true", help="실제 write(미지정=dry-run·카운트만)")
    ap.add_argument("--limit", type=int, default=0, help="이번 run 최대 링크 수(0=전량·apply 전용)")
    ap.add_argument("--batch-size", type=int, default=500)
    ap.add_argument("--max-spin", type=float, default=60.0)
    args = ap.parse_args(argv)

    conn = get_connection(args.db)
    lock = ShlockBatch(_LOCK_PATH, max_spin=args.max_spin)
    mode = "APPLY(write)" if args.apply else "DRY-RUN(write 0)"
    print(f"# backfill_derived_apartments — {mode} db={args.db}")
    total = {"derivable": 0, "nonderivable": 0, "buildings": 0, "linked": 0}
    for table in TABLES:
        res = run_backfill(
            conn, table=table, apply=args.apply, limit=args.limit,
            batch_size=args.batch_size, lock=lock,
        )
        print(f"  {table:<18} 신규건물 {res['buildings']:>7,} · 도출가능 {res['derivable']:>8,} · "
              f"도출불가 {res['nonderivable']:>8,} · 링크 {res['linked']:>8,}")
        for k in ("derivable", "nonderivable", "buildings", "linked"):
            total[k] += res[k]
    print(f"  {'합계':<18} 도출가능 {total['derivable']:,} · 도출불가 {total['nonderivable']:,} · "
          f"링크 {total['linked']:,}")
    if not args.apply:
        print("  (dry-run — complex/txn write 0. --apply로만 실제 생성[B-2b].)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
