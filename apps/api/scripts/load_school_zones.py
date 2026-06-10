"""배정 초등 통학구역 적재 (school-2) — SHP polygon point-in-polygon → school_assignment.

오프라인·키리스: data/school_zone/*.shp(EPSG:5186) + data/school_zone_link.csv(연계)를 읽어
ZoneIndex(STRtree) 구성 후 좌표보유 단지 point-in-polygon → 배정 초등 적재. **컴퓨트 시점 외부
호출 0**. 단, 같은 sqlite를 쓰는 거래·enrich·poi·school 배치와 직렬화해야 하므로 청크 단위로 공유
`.ingest.lock`(C47) acquire/release(굶김·lock 충돌 방지). 좌표 read·school_assignment write만 →
지문 163df7…·counts 불변. resumable(sentinel 포함 done-set). graceful(SHP/CSV 파싱 실패→중단·보존).

데이터는 fetch_school_data.py가 깔아둠(반기). 갱신 시 재실행.

    uv run python scripts/load_school_zones.py                 # 전국 배정 적재(resume)
    uv run python scripts/load_school_zones.py --limit 5000    # 부분
"""

from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime
from pathlib import Path

import _bootstrap  # noqa: F401  (apps/api를 sys.path에)
from refill_kapt_fields import ShlockBatch

from app.school.assignment import enrich_assignment, load_zone_index
from app.store.db import DEFAULT_DB_PATH, get_connection, init_db

DATA = Path(DEFAULT_DB_PATH).resolve().parent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="load_school_zones")
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--shp", default=str(DATA / "school_zone" / "*.shp"))
    ap.add_argument("--link", default=str(DATA / "school_zone_link.csv"))
    ap.add_argument("--limit", type=int, default=200_000, help="이번 run 처리 단지 상한(resume)")
    ap.add_argument("--chunk", type=int, default=200, help="락 보유당 complex 수")
    ap.add_argument("--inter-batch-sleep", type=float, default=0.3)
    ap.add_argument("--lock", default=None)
    ap.add_argument("--max-spin", type=float, default=60.0)
    args = ap.parse_args(argv)

    for p in (args.link,):
        if not Path(p).exists() or not list(Path(args.shp).parent.glob("*.shp")):
            print(f"✗ 통학구역 데이터 없음 — fetch_school_data.py 먼저 ({args.shp})")
            return 1

    index = load_zone_index(args.shp, args.link)
    print("통학구역 인덱스 구성: SHP polygon + 연계(초등) 로드 완료")

    conn = get_connection(args.db)
    init_db(conn)  # school_assignment 테이블 멱등 보장(additive)
    lock_path = args.lock or str(DATA / ".ingest.lock")
    lock = ShlockBatch(lock_path, max_spin=args.max_spin)

    remaining = args.limit
    t_assigned = t_none = t_proc = 0
    while remaining > 0:
        with lock() as acquired:  # type: ignore[attr-defined]
            if not acquired:
                print("[skip] cron 장시간 점유 — 이번 run 양보(resume)")
                break
            r = enrich_assignment(
                conn, index, now=datetime.now(UTC), limit=min(args.chunk, remaining)
            )
        t_assigned += r["assigned"]
        t_none += r["none"]
        t_proc += r["processed"]
        remaining -= r["processed"]
        if r["processed"] == 0:
            print("[done] 더 처리할 미계산 complex 없음.")
            break
        time.sleep(args.inter_batch_sleep)
    print(f"[ok] 배정 적재 — 처리 {t_proc} · 배정 {t_assigned} · 폴리곤밖(none) {t_none}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
