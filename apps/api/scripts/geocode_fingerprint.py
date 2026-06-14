"""아파트 geocode 지문 — 좌표보존 불변식의 표준 베이스라인. (enrich-1b)

enrich/적재가 아파트 lat/lng를 건드리지 않았음을 before==after로 증명하는 결정론 지문.
지문 = `sha256( "complex_id|lat|lng" 줄들, property_type='apartment', ORDER BY complex_id )[:16]`.

표준 베이스라인 **163df7cd7e6a3cc2**(아파트 22,028, fba2fb1 시점). 과거 의뢰서의
`4907f64de2b44fed`는 산출 레시피가 문서/코드에 없어 재현 불가 → 이 코드화 지문으로 대체·표준화.

    uv run python scripts/geocode_fingerprint.py                       # 현재 지문 출력
    uv run python scripts/geocode_fingerprint.py --db path/to.db       # 지정 DB
    uv run python scripts/geocode_fingerprint.py --expect 163df7cd7e6a3cc2  # 불변검증(exit1=불일치)
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3

import _bootstrap  # noqa: F401  (apps/api를 sys.path에)

from app.store.db import DEFAULT_DB_PATH, get_connection

# 베이스라인 — fba2fb1(enrich-1) 시점 아파트 22,028 좌표. enrich는 좌표 무접촉이라 불변이어야 함.
BASELINE = "163df7cd7e6a3cc2"


def geocode_fingerprint(conn: sqlite3.Connection, *, kapt_only: bool = False) -> tuple[str, int]:
    """아파트 (complex_id, lat, lng) 결정론 지문 + 아파트 수 반환.

    apartment = property_type='apartment'(init_db가 레거시 NULL을 백필하므로 IS NULL 불요).
    줄 = 'complex_id|lat|lng'(미지오코딩 NULL은 'None' 센티넬 — str 렌더), 키 정렬, sha256[:16].
    이 렌더가 BASELINE 163df7cd7e6a3cc2를 재현한다(좌표값 자체는 불변 — 렌더 일관성만 중요).

    kapt_only=True면 K-apt 단지코드(콜론 없음)만 — building-add(#6-③B) 도출 아파트('ap:' 접두)를
    제외해 기존 22,028 서브셋 무드리프트를 검증한다. 기본(False)은 전체 apartment(드리프트 0).
    """
    where = "property_type='apartment'"
    if kapt_only:
        where += " AND complex_id NOT LIKE '%:%'"  # K-apt 단지코드만(도출 'ap:'·ro:/of: 제외)
    rows = conn.execute(
        f"SELECT complex_id, lat, lng FROM complex WHERE {where} ORDER BY complex_id"
    ).fetchall()
    blob = "\n".join(f"{r[0]}|{r[1]}|{r[2]}" for r in rows)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16], len(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="geocode_fingerprint")
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--expect", default=None, help="기대 지문 — 불일치면 exit 1(불변 검증)")
    ap.add_argument(
        "--kapt-only", action="store_true",
        help="K-apt 단지코드만(도출 'ap:' 제외) — building-add 후 22,028 서브셋 무드리프트 검증",
    )
    args = ap.parse_args(argv)

    conn = get_connection(args.db)
    fp, n = geocode_fingerprint(conn, kapt_only=args.kapt_only)
    print(f"geocode_fingerprint={fp} (apartments={n:,})")
    if args.expect is not None and fp != args.expect:
        print(f"MISMATCH — 기대 {args.expect} ≠ 실제 {fp} (좌표 변경 감지!)")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
