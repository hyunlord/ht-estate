"""비-아파트 건물 도심-우선 바운드 geocode — 한 invocation에 LIMIT개만(청크). (P5-1b-run)

`backfill_geocode.sh`가 이걸 청크당 공유락 보유 하에 반복 호출한다. 글로벌 1회커밋인
`backfill_coords`(아파트 포함)와 달리, lat-NULL nonapt만 도심우선 LIMIT개 처리·증분커밋·
cap-graceful(Kakao 한도 시 stopped). 아파트 무접촉. 출력 끝줄을 셸이 파싱(remaining/stopped).

    uv run python scripts/geocode_nonapt.py --limit 300 --interval 0.25
"""

from __future__ import annotations

import argparse

import _bootstrap  # noqa: F401  (side-effect: apps/api를 sys.path에)

from app.geo.geocoder import geocode
from app.ingest import GEO_SOURCE
from app.settings import get_kakao_key
from app.store.db import DEFAULT_DB_PATH, get_connection, init_db
from app.store.geo_repo import geocode_nonapt_pending
from app.throttle import Throttle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="geocode_nonapt", description="비-아파트 도심우선 바운드 geocode(청크·증분)"
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite 경로")
    parser.add_argument("--limit", type=int, default=300, help="이번 청크 geocode 최대 건수")
    parser.add_argument("--interval", type=float, default=0.25, help="Kakao 호출 간 최소 간격(초)")
    args = parser.parse_args(argv)

    conn = get_connection(args.db)
    init_db(conn)
    key = get_kakao_key()
    throttle = Throttle(args.interval) if args.interval > 0 else None
    res = geocode_nonapt_pending(
        conn,
        lambda addr: geocode(addr, api_key=key),
        limit=args.limit,
        geo_source=GEO_SOURCE,
        throttle=throttle,
    )
    # 끝줄 — backfill_geocode.sh가 remaining/stopped 파싱.
    print(
        f"[geocode-chunk] geocoded={res['geocoded']} considered={res['considered']} "
        f"remaining={res['remaining']} stopped={res['stopped']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
