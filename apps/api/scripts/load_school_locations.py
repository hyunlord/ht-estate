"""학교 거리 근접 적재 (school-1) — 전국초중등학교위치표준데이터(15021148) CSV → school_proximity.

**오프라인·키리스**: data/school_locations.csv(고정경로)를 읽어 SchoolIndex(인메모리) 구성 후
단지별·level별 최근접/개수 계산·적재. **컴퓨트 시점 외부 호출 0**(정부 좌표 직제공) — poi와 달리
쿼터·429 없음. 단, **같은 sqlite를 쓰는 거래·enrich·poi 배치와 직렬화**해야 하므로 청크 단위로 공유
`.ingest.lock`(C47) spin-acquire/release(굶김 방지·lock 충돌 0). 좌표 read·school write만 →
지문 163df7…·counts 불변. resumable(이미 적재 단지 skip).

CSV 받기(키리스, 1회): data.go.kr/data/15021148 또는 schoolzone.emac.kr →
CSV를 `apps/api/data/school_locations.csv`로 저장. 반기 갱신 시 재저장+재실행.

    uv run python scripts/load_school_locations.py                 # 전국 적재(resume)
    uv run python scripts/load_school_locations.py --limit 5000    # 부분
"""

from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime
from pathlib import Path

import _bootstrap  # noqa: F401  (apps/api를 sys.path에)
from refill_kapt_fields import ShlockBatch

from app.school.locations import LEVEL_ORDER, SchoolIndex, load_schools
from app.school.runner import enrich_school
from app.store.db import DEFAULT_DB_PATH, get_connection, init_db

DEFAULT_CSV = str(Path(DEFAULT_DB_PATH).resolve().parent / "school_locations.csv")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="load_school_locations")
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--csv", default=DEFAULT_CSV, help="15021148 표준데이터 CSV 경로")
    ap.add_argument("--limit", type=int, default=200_000, help="이번 run 처리 단지 상한(resume)")
    ap.add_argument("--chunk", type=int, default=200, help="락 보유당 complex 수(배치)")
    ap.add_argument("--inter-batch-sleep", type=float, default=0.3, help="청크 사이 release 창(초)")
    ap.add_argument("--lock", default=None, help="공유 락(기본 <db디렉토리>/.ingest.lock)")
    ap.add_argument("--max-spin", type=float, default=60.0)
    args = ap.parse_args(argv)

    if not Path(args.csv).exists():
        print(
            f"✗ CSV 없음: {args.csv}\n"
            "  data.go.kr/data/15021148 또는 schoolzone.emac.kr에서 받아 이 경로에 저장."
        )
        return 1

    schools = load_schools(args.csv)
    index = SchoolIndex(schools)
    by_level = {lvl: sum(1 for s in schools if s.level == lvl) for lvl in LEVEL_ORDER}
    print(f"학교 적재(운영중·유효좌표): {len(schools)} — " + " ".join(
        f"{lvl}={by_level[lvl]}" for lvl in LEVEL_ORDER
    ))

    conn = get_connection(args.db)
    init_db(conn)  # school_proximity 테이블 멱등 보장(additive)
    lock_path = args.lock or str(Path(args.db).resolve().parent / ".ingest.lock")
    lock = ShlockBatch(lock_path, max_spin=args.max_spin)

    remaining = args.limit
    total_c = total_rows = 0
    while remaining > 0:
        with lock() as acquired:  # type: ignore[attr-defined]
            if not acquired:
                print("[skip] cron 장시간 점유 — 이번 run 양보(resume)")
                break
            r = enrich_school(
                conn, index, now=datetime.now(UTC), limit=min(args.chunk, remaining)
            )
        total_c += int(r["complexes"])
        total_rows += int(r["rows"])
        remaining -= int(r["complexes"])
        if int(r["complexes"]) == 0:
            print("[done] 더 처리할 미적재 complex 없음.")
            break
        time.sleep(args.inter_batch_sleep)  # cron/배치 release 창
    print(f"[ok] 적재 — 단지 {total_c} · 행 {total_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
