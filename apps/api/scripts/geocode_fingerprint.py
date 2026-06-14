"""아파트 geocode 지문 — 좌표보존 불변식의 표준 베이스라인. (enrich-1b)

enrich/적재가 아파트 lat/lng를 건드리지 않았음을 before==after로 증명하는 결정론 지문.
지문 = `sha256( "complex_id|lat|lng" 줄들, property_type='apartment', ORDER BY complex_id )[:16]`.

표준 베이스라인 **e190614fa353cbcf**(아파트 41,691, #6-③B building-add 후). K-apt 마스터
22,028 서브셋은 `--kapt-only`로 **163df7cd7e6a3cc2** 무드리프트 유지(좌표 무접촉 증명).

전환 기록(C98·#6-③B building-add): `163df7cd7e6a3cc2`(apt 22,028) → `e190614fa353cbcf`
(apt 41,691). 사유: 거래-도출 아파트 +19,663(`ap:` 접두·19,614 지오코딩·49 무결과 NULL) 추가로
complex 172,879 → 192,542·apartment 22,028 → 41,691. 기존 K-apt 좌표는 무드리프트(`--kapt-only`
== 163df7cd7e6a3cc2 불변). 과거 `4907f64de2b44fed`(레시피 부재 재현불가)는 163df7로 대체됐었음.

    uv run python scripts/geocode_fingerprint.py                          # 현재 전체 지문
    uv run python scripts/geocode_fingerprint.py --expect e190614fa353cbcf  # 전체 불변검증
    uv run python scripts/geocode_fingerprint.py --kapt-only --expect 163df7cd7e6a3cc2
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3

import _bootstrap  # noqa: F401  (apps/api를 sys.path에)

from app.store.db import DEFAULT_DB_PATH, get_connection

# 베이스라인 — #6-③B building-add 후 아파트 41,691(K-apt 22,028 + 도출 ap: 19,663). enrich/적재는
# 좌표 무접촉이라 불변이어야 함. K-apt 서브셋 무드리프트는 --kapt-only(== KAPT_BASELINE)로 검증.
BASELINE = "e190614fa353cbcf"
# K-apt 마스터 22,028 서브셋(콜론 없는 단지코드) 무드리프트 floor — building-add 전반 불변(C98).
KAPT_BASELINE = "163df7cd7e6a3cc2"


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
