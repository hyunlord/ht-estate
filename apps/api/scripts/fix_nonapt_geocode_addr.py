"""비-아파트 건물 geocode 주소 보정 — '법정동 지번' → '시도 시군구 법정동 지번' (동명중복 해소).

배경: nonapt 건물은 roadNm이 없어 '법정동 지번'으로 geocode하는데, 시·도를 가로지르는 동/구명
중복(부산 중구 영주동 vs 경북 영주시)으로 Kakao가 타도시로 오지오코딩한다. `_geocodable_addr`
픽스(시군구코드→'시도 시군구' 프리픽스)는 *신규* 적재에만 적용 → 이미 적재된 건물은 이 도구로
in-place 보정한다(전체 재적재는 resume 레저가 skip해 무의미). complex_id
(`pt:sgg:dong:jibun:name`)에서 sgg/dong/jibun 복원 → `_geocodable_addr`와 동일 규칙으로 재구성.

규율:
- **멱등** — 이미 보정된 행(road_addr==재구성값)은 skip. 여러 번 실행 안전.
- **보정 시 lat NULL화** — 주소가 바뀐 건물은 (혹시 이전 오지오코딩분이 있으면) 재geocode 대상이
  되게 lat/lng/geo_*를 비운다. 아직 미지오코딩이면 no-op.
- **아파트 무접촉** — property_type ∈ {rowhouse, officetel}만.

    uv run python scripts/fix_nonapt_geocode_addr.py            # 보정
    uv run python scripts/fix_nonapt_geocode_addr.py --dry-run  # 변경 건수만
"""

from __future__ import annotations

import argparse
import sqlite3

import _bootstrap  # noqa: F401  (side-effect: apps/api를 sys.path에)

from app.store.db import DEFAULT_DB_PATH, get_connection, init_db
from app.store.regions import sigungu_label

_NONAPT = ("rowhouse", "officetel")


def _rebuilt_addr(complex_id: str) -> str | None:
    """complex_id(`pt:sgg:dong:jibun:name`)에서 '시도 시군구 법정동 지번' 재구성. 미매핑이면 None.

    dong/jibun엔 콜론이 없어 split(':')[1:4]가 안전(name은 마지막이라 콜론 포함해도 무관).
    """
    parts = complex_id.split(":")
    if len(parts) < 4:
        return None
    sgg, dong, jibun = parts[1], parts[2], parts[3]
    label = sigungu_label(sgg)
    if not label:
        return None
    return " ".join(p for p in (label, dong, jibun) if p and p != "?").strip()


def fix_addresses(conn: sqlite3.Connection, *, dry_run: bool = False) -> tuple[int, int]:
    """nonapt 건물 주소 보정. (보정, skip) 반환."""
    placeholders = ",".join("?" * len(_NONAPT))
    rows = conn.execute(
        f"SELECT complex_id, road_addr FROM complex WHERE property_type IN ({placeholders})",
        _NONAPT,
    ).fetchall()
    fixed = skipped = 0
    for complex_id, road_addr in rows:
        new = _rebuilt_addr(complex_id)
        if new is None or new == road_addr:
            skipped += 1
            continue
        if not dry_run:
            # 주소 보정 + lat NULL화(이전 오지오코딩 있으면 재geocode 대상으로). geo_source도 비움.
            conn.execute(
                "UPDATE complex SET road_addr = ?, legal_addr = ?, "
                "lat = NULL, lng = NULL, geo_source = NULL WHERE complex_id = ?",
                (new, new, complex_id),
            )
        fixed += 1
    if not dry_run:
        conn.commit()
    return fixed, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fix_nonapt_geocode_addr", description="비-아파트 건물 geocode 주소 보정(멱등)"
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite 경로")
    parser.add_argument("--dry-run", action="store_true", help="변경 건수만 출력(쓰기 없음)")
    args = parser.parse_args(argv)

    conn = get_connection(args.db)
    init_db(conn)
    fixed, skipped = fix_addresses(conn, dry_run=args.dry_run)
    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}nonapt 주소 보정 — 변경 {fixed} · skip {skipped}(이미 보정/미매핑)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
