"""pipeline-state: 적재 파이프라인 자기서술 표 출력(read-only).

`uv run python scripts/pipeline_status.py` → pipeline_state 원장을 표로. "얼마나 됐지/정상인지/
언제 시작"을 한 쿼리로(git·메모리 불요). pipeline_state만 SELECT → canonical 무접촉.
`--refresh`면 출력 전 bootstrap_pipeline_state로 provenance서 최신화(여전히 META만 write)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.store.db import DEFAULT_DB_PATH, get_connection, init_db  # noqa: E402
from app.store.pipeline_state import (  # noqa: E402
    bootstrap_pipeline_state,
    read_pipeline_state,
)


def _pct(current: object, target: object) -> str:
    if not isinstance(current, int) or not isinstance(target, int) or target <= 0:
        return "—"
    return f"{100 * current / target:.1f}%"


def _short(ts: object) -> str:
    return str(ts)[:16] if ts else "—"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pipeline_status")
    p.add_argument("--db", default=str(DEFAULT_DB_PATH))
    p.add_argument("--refresh", action="store_true", help="출력 전 provenance서 최신화(META write)")
    args = p.parse_args(argv)

    conn = get_connection(args.db)
    if args.refresh:
        init_db(conn)  # 스키마 보장 + bootstrap
        bootstrap_pipeline_state(conn)
    rows = read_pipeline_state(conn)
    if not rows:
        print("pipeline_state 비어있음 — init_db(또는 --refresh)로 부트스트랩 필요.")
        return 0

    hdr = (
        f"{'pipeline':<18} {'status':<9} {'progress':>16} {'%':>6} "
        f"{'born':<16} {'last_run':<16} {'ETA':<16}"
    )
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        cur, tgt = r["current_count"], r["target_count"]
        prog = f"{cur:,}/{tgt:,}" if isinstance(tgt, int) else f"{cur:,}/—"
        print(
            f"{str(r['name']):<18} {str(r['status']):<9} {prog:>16} {_pct(cur, tgt):>6} "
            f"{_short(r['introduced_at']):<16} {_short(r['last_run_at']):<16} "
            f"{_short(r['expected_complete_at']):<16}"
        )
        print(f"    └ metric: {r['metric']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
