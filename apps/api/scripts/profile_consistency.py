"""정합성 프로파일러 — canonical DB 적재 재검증(#6)의 measure-before-build 1스텝.

오직 SELECT/PRAGMA만 실행하는 **read-only** 리포터(쓰기 구문 일절 없음 → 지문/counts 자명 불변).
수정 전/후 동일 스크립트를 재실행해 정합성 회복을 증명하는 재사용 베이스라인.
geocode_fingerprint.py·pipeline_status.py와 같은 DB 오픈 컨벤션(apps/api에서 실행).

리포트(stdout): A 카운트·불변식 / B 조인 정합성(코어) / C 지역 라벨·변종 /
D 좌표 공백 / E enrich 레이어 커버리지 / F 참조 무결성·고아 / G 값 새너티.

    uv run python scripts/profile_consistency.py
    uv run python scripts/profile_consistency.py --db path/to.db
"""

from __future__ import annotations

import argparse
import sqlite3

import _bootstrap  # noqa: F401  (apps/api를 sys.path에)

from app.store.db import DEFAULT_DB_PATH, get_connection
from app.store.regions import sigungu_label

# ── repo.py(C90)와 동일한 시도/시군구 주소-파싱 식 — 지도 클러스터가 보는 라벨과 1:1 일치.
# 컬럼(sido 미백필·sigungu 부분백필)을 COALESCE 우선, 비면 road/legal_addr 토큰 파싱 폴백.
_ADDR = "COALESCE(NULLIF(c.road_addr, ''), c.legal_addr, '')"
_SIDO_PARSE = f"substr({_ADDR}, 1, instr({_ADDR} || ' ', ' ') - 1)"
_SIDO = f"COALESCE(NULLIF(c.sido, ''), {_SIDO_PARSE})"
_SIGUNGU_PARSE = (
    f"substr(substr({_ADDR}, instr({_ADDR}, ' ') + 1), 1, "
    f"instr(substr({_ADDR}, instr({_ADDR}, ' ') + 1) || ' ', ' ') - 1)"
)
_SIGUNGU = f"COALESCE(NULLIF(c.sigungu, ''), {_SIGUNGU_PARSE})"

# 매매·전월세는 조인 경로가 달라(전월세는 backfill_rent_bjd 후 매칭·더 약함) 분리 측정.
TXN_TABLES = [
    ("매매(transaction)", '"transaction"'),
    ("전월세(rent_transaction)", "rent_transaction"),
]

# 자식 테이블 — complex_id가 complex에 없으면 dangling(과거 적재분 검증·0 기대).
CHILD_TABLES = [
    '"transaction"', "rent_transaction", "enrichment", "poi_proximity",
    "school_proximity", "school_assignment", "unit_type", "review_chunk",
]


def _hr(title: str) -> None:
    print(f"\n{'═' * 78}\n{title}\n{'═' * 78}")


def _sub(title: str) -> None:
    print(f"\n── {title}")


def _pct(n: int, d: int) -> str:
    return f"{100.0 * n / d:6.2f}%" if d else "  n/a "


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row and row[0] is not None else 0


def section_a(conn: sqlite3.Connection) -> int:
    _hr("A. 카운트·불변식 새너티")
    n_cx = _scalar(conn, "SELECT count(*) FROM complex")
    n_txn = _scalar(conn, 'SELECT count(*) FROM "transaction"')
    n_rent = _scalar(conn, "SELECT count(*) FROM rent_transaction")
    print(f"  complex            = {n_cx:>10,}   (기대 172,879)")
    print(f"  transaction(매매)  = {n_txn:>10,}   (기대 671,229)")
    print(f"  rent_transaction   = {n_rent:>10,}   (기대 1,551,334)")

    _sub("property_type 분포(NULL 버킷 포함)")
    for pt, cnt in conn.execute(
        "SELECT COALESCE(NULLIF(property_type,''),'(NULL)') pt, count(*) c "
        "FROM complex GROUP BY pt ORDER BY c DESC"
    ).fetchall():
        print(f"    {pt:<14} {cnt:>10,}  {_pct(cnt, n_cx)}")

    _sub("좌표 보유(lat·lng NOT NULL)")
    n_geo = _scalar(conn, "SELECT count(*) FROM complex WHERE lat IS NOT NULL AND lng IS NOT NULL")
    n_apt_geo = _scalar(
        conn,
        "SELECT count(*) FROM complex WHERE lat IS NOT NULL AND lng IS NOT NULL "
        "AND property_type='apartment'",
    )
    print(f"    전체 좌표 보유   = {n_geo:>10,}  {_pct(n_geo, n_cx)}")
    print(f"    apartment 좌표   = {n_apt_geo:>10,}   (지문 기준 22,028 교차)")
    return n_cx


def section_b(conn: sqlite3.Connection) -> None:
    _hr("B. ★조인 정합성 (코어) — 매매·전월세 분리")
    for label, tbl in TXN_TABLES:
        _sub(f"{label}")
        total = _scalar(conn, f"SELECT count(*) FROM {tbl}")
        matched = _scalar(conn, f"SELECT count(complex_id) FROM {tbl}")  # count()는 NOT NULL만
        orphan = total - matched
        print(f"    총행 {total:>10,} | 매칭 {matched:>10,} ({_pct(matched, total)}) | "
              f"고아(NULL) {orphan:>10,} ({_pct(orphan, total)})")

        # match_confidence 히스토그램 — 0.85(이름퍼지)·0.9(지번/containment)·0.95(도로)·1.0 경계.
        hist = conn.execute(
            "SELECT "
            "sum(CASE WHEN cid IS NULL THEN 1 ELSE 0 END), "
            "sum(CASE WHEN cid IS NOT NULL AND mc IS NULL THEN 1 ELSE 0 END), "
            "sum(CASE WHEN mc > 0 AND mc < 0.85 THEN 1 ELSE 0 END), "
            "sum(CASE WHEN mc >= 0.85 AND mc < 0.90 THEN 1 ELSE 0 END), "
            "sum(CASE WHEN mc >= 0.90 AND mc < 1.0 THEN 1 ELSE 0 END), "
            "sum(CASE WHEN mc = 1.0 THEN 1 ELSE 0 END), "
            "sum(CASE WHEN mc = 0.9 THEN 1 ELSE 0 END) "
            f"FROM (SELECT complex_id cid, match_confidence mc FROM {tbl})"
        ).fetchone()
        names = ["미매칭(NULL)", "매칭·conf없음", "[<0.85]", "[0.85,0.90)",
                 "[0.90,1.0)", "=1.0(도로/완전)", "  └ =0.9(지번/포함)"]
        print("    match_confidence 히스토그램:")
        for nm, v in zip(names, hist, strict=True):
            v = v or 0
            print(f"      {nm:<18} {v:>10,}  {_pct(v, total)}")

        _sub(f"{label} 미매칭 apt_name_raw top-20(빈도순)")
        rows = conn.execute(
            f"SELECT COALESCE(apt_name_raw,'(NULL)') nm, count(*) c FROM {tbl} "
            f"WHERE complex_id IS NULL GROUP BY nm ORDER BY c DESC LIMIT 20"
        ).fetchall()
        if not rows:
            print("      (미매칭 0행)")
        for nm, c in rows:
            print(f"      {c:>7,}  {nm[:40]}")


def section_c(conn: sqlite3.Connection, n_cx: int) -> None:
    _hr("C. 지역 라벨 채움·변종")
    _sub("complex 컬럼별 비-NULL·비-빈 카운트")
    for col in ["sido", "sigungu", "eupmyeon", "dong", "bjd_code", "legal_addr", "road_addr"]:
        cnt = _scalar(
            conn, f"SELECT count(*) FROM complex WHERE {col} IS NOT NULL AND {col} <> ''"
        )
        print(f"    {col:<12} {cnt:>10,}  {_pct(cnt, n_cx)}")

    _sub("시도 distinct + 행수 (repo.py 동일 파싱 — 지도가 보는 값·변종 전부 노출)")
    for sido, cnt in conn.execute(
        f"SELECT {_SIDO} s, count(*) c FROM complex c GROUP BY s ORDER BY c DESC"
    ).fetchall():
        print(f"    {(sido or '(빈)'):<16} {cnt:>10,}")

    _sub("시군구 distinct + 행수 (repo.py 동일 파싱)")
    sgg_rows = conn.execute(
        f"SELECT {_SIGUNGU} s, count(*) c FROM complex c GROUP BY s ORDER BY c DESC"
    ).fetchall()
    print(f"    distinct 시군구 = {len(sgg_rows):,}")
    print("    top 30:")
    for s, c in sgg_rows[:30]:
        print(f"      {(s or '(빈)'):<18} {c:>8,}")
    merged = [(s, c) for s, c in sgg_rows if s and len(s) >= 5 and s.endswith("구")]
    print(f"    ⚠ 머지형 의심(길이≥5·'구' 끝·예 고양덕양구) = {len(merged)}개:")
    for s, c in merged[:20]:
        print(f"      {s:<18} {c:>8,}")

    _sub("bjd_code 보유 % + regions CSV 해소 가능 %(후속 코드→표준명 백필 타당성)")
    bjd_have = _scalar(
        conn, "SELECT count(*) FROM complex WHERE bjd_code IS NOT NULL AND length(bjd_code) >= 5"
    )
    print(f"    bjd_code(≥5자리) 보유 = {bjd_have:>10,}  {_pct(bjd_have, n_cx)}")
    sgg_counts = conn.execute(
        "SELECT substr(bjd_code,1,5) sgg, count(*) c FROM complex "
        "WHERE bjd_code IS NOT NULL AND length(bjd_code) >= 5 GROUP BY sgg"
    ).fetchall()
    resolvable = sum(c for sgg, c in sgg_counts if sigungu_label(sgg) is not None)
    distinct_sgg = len(sgg_counts)
    distinct_ok = sum(1 for sgg, _ in sgg_counts if sigungu_label(sgg) is not None)
    print(f"    regions.sigungu_label 해소 가능(시도/시군구 표준명 회수) = {resolvable:>10,}  "
          f"{_pct(resolvable, n_cx)}")
    print(f"    distinct sgg_cd = {distinct_sgg:,} 중 해소 = {distinct_ok:,}")


def section_d(conn: sqlite3.Connection) -> None:
    _hr("D. 좌표 공백 (property_type별 — 지도 비노출 단지)")
    for pt, miss, tot in conn.execute(
        "SELECT COALESCE(NULLIF(property_type,''),'(NULL)') pt, "
        "sum(CASE WHEN lat IS NULL OR lng IS NULL THEN 1 ELSE 0 END) miss, count(*) tot "
        "FROM complex GROUP BY pt ORDER BY tot DESC"
    ).fetchall():
        print(f"    {pt:<14} 결측 {miss:>8,} / {tot:>8,}  ({_pct(miss, tot)} 미지오코딩)")


def section_e(conn: sqlite3.Connection, n_cx: int) -> None:
    _hr("E. enrich 레이어 커버리지 (distinct complex_id vs 모집단)")

    def cov(tbl: str, where: str = "") -> int:
        return _scalar(conn, f"SELECT count(DISTINCT complex_id) FROM {tbl} {where}")

    _sub("poi_proximity (category별 distinct 단지)")
    for cat, d in conn.execute(
        "SELECT category, count(DISTINCT complex_id) FROM poi_proximity "
        "GROUP BY category ORDER BY 2 DESC"
    ).fetchall():
        print(f"    {cat:<8} {d:>10,}  {_pct(d, n_cx)}")
    print(f"    └ 전체 distinct = {cov('poi_proximity'):,}  {_pct(cov('poi_proximity'), n_cx)}")

    _sub("school_proximity (level별)")
    for lv, d in conn.execute(
        "SELECT level, count(DISTINCT complex_id) FROM school_proximity "
        "GROUP BY level ORDER BY 2 DESC"
    ).fetchall():
        print(f"    {lv:<8} {d:>10,}  {_pct(d, n_cx)}")

    _sub("기타 레이어 distinct 단지")
    for nm, tbl in [
        ("school_assignment", "school_assignment"),
        ("unit_type", "unit_type"),
        ("review_chunk", "review_chunk"),
    ]:
        d = cov(tbl)
        print(f"    {nm:<18} {d:>10,}  {_pct(d, n_cx)}")
    n_chunks = _scalar(conn, "SELECT count(*) FROM review_chunk")
    print(f"    review_chunk 총 청크수 = {n_chunks:,}")

    _sub("enrichment (attribute별 distinct 단지)")
    for attr, d in conn.execute(
        "SELECT COALESCE(attribute,'(NULL)') a, count(DISTINCT complex_id) FROM enrichment "
        "GROUP BY a ORDER BY 2 DESC"
    ).fetchall():
        print(f"    {attr:<20} {d:>10,}  {_pct(d, n_cx)}")

    _sub("complex 인라인 enrich 컬럼 비-NULL 단지 (건축물대장·K-apt V4)")
    for col in ["ledger_pk", "heat_type", "sale_type", "builder", "elevator_count",
                "household_count", "approval_date", "parking_total", "amenities_raw"]:
        cnt = _scalar(conn, f"SELECT count(*) FROM complex WHERE {col} IS NOT NULL AND {col} <> ''")
        print(f"    {col:<16} {cnt:>10,}  {_pct(cnt, n_cx)}")


def section_f(conn: sqlite3.Connection) -> None:
    _hr("F. 참조 무결성·고아")
    print(f"  PRAGMA foreign_keys = {_scalar(conn, 'PRAGMA foreign_keys')}")
    fk_viol = len(conn.execute("PRAGMA foreign_key_check").fetchall())
    print(f"  PRAGMA foreign_key_check 위반 행 = {fk_viol}  (0 기대)")

    _sub("dangling complex_id (자식.complex_id가 complex에 없음 · 0 기대)")
    for tbl in CHILD_TABLES:
        dangling = _scalar(
            conn,
            f"SELECT count(*) FROM {tbl} t LEFT JOIN complex c ON t.complex_id = c.complex_id "
            f"WHERE t.complex_id IS NOT NULL AND c.complex_id IS NULL",
        )
        flag = "" if dangling == 0 else "  ⚠"
        print(f"    {tbl:<22} dangling = {dangling:>8,}{flag}")

    _sub("pipeline_state 전행 덤프 (자기서술 vs 실측 교차)")
    rows = conn.execute(
        "SELECT name, introduced_at, current_count, target_count, metric, status, last_run_at "
        "FROM pipeline_state ORDER BY name"
    ).fetchall()
    if not rows:
        print("    (pipeline_state 0행)")
    for r in rows:
        nm, intro, cur, tgt, metric, status, last = r
        cur_s = f"{cur:,}" if isinstance(cur, int) else str(cur)
        tgt_s = f"{tgt:,}" if isinstance(tgt, int) else ("∞" if tgt is None else str(tgt))
        print(f"    {nm:<20} {str(status or ''):<10} {cur_s:>12} / {tgt_s:<12} "
              f"[{metric or ''}]")
        print(f"        intro={intro}  last_run={last}")


def section_g(conn: sqlite3.Connection, n_cx: int) -> None:
    _hr("G. 값 새너티 (라이트)")
    _sub("매매(transaction)")
    row = conn.execute(
        'SELECT min(price), max(price), '
        'sum(CASE WHEN price <= 0 THEN 1 ELSE 0 END), '
        'min(net_area), max(net_area), '
        'sum(CASE WHEN net_area <= 0 THEN 1 ELSE 0 END), '
        'sum(CASE WHEN net_area > 1000 THEN 1 ELSE 0 END), '
        'min(deal_date), max(deal_date), min(build_year), max(build_year) '
        'FROM "transaction"'
    ).fetchone()
    print(f"    price(만원)  min={row[0]:,} max={row[1]:,}  ≤0건={row[2] or 0:,}")
    print(f"    net_area(㎡) min={row[3]} max={row[4]}  "
          f"≤0건={row[5] or 0:,}  >1000건={row[6] or 0:,}")
    print(f"    deal_date    {row[7]} ~ {row[8]}")
    print(f"    build_year   min={row[9]} max={row[10]}")

    _sub("전월세(rent_transaction)")
    row = conn.execute(
        "SELECT sum(CASE WHEN deposit <= 0 THEN 1 ELSE 0 END), "
        "sum(CASE WHEN monthly_rent < 0 THEN 1 ELSE 0 END), "
        "sum(CASE WHEN deposit = 0 AND monthly_rent = 0 THEN 1 ELSE 0 END) FROM rent_transaction"
    ).fetchone()
    print(f"    deposit ≤0건={row[0] or 0:,}  monthly_rent <0건={row[1] or 0:,}  "
          f"(deposit=0 & monthly=0)건={row[2] or 0:,}")
    print("    rent_type 분포:")
    for rt, c in conn.execute(
        "SELECT COALESCE(NULLIF(rent_type,''),'(NULL)') rt, count(*) c "
        "FROM rent_transaction GROUP BY rt ORDER BY c DESC"
    ).fetchall():
        print(f"      {rt:<12} {c:>12,}")

    _sub("논리중복 — 동일 (name, road_addr)가 서로 다른 complex_id로 잡힌 그룹 수")
    dup = _scalar(
        conn,
        "SELECT count(*) FROM (SELECT name, road_addr FROM complex "
        "WHERE name IS NOT NULL AND road_addr IS NOT NULL AND road_addr <> '' "
        "GROUP BY name, road_addr HAVING count(DISTINCT complex_id) > 1)",
    )
    print(f"    중복 그룹 = {dup:,}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="profile_consistency")
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    args = ap.parse_args(argv)

    conn = get_connection(args.db)
    print(f"# profile_consistency — db={args.db}")
    print("# read-only(SELECT/PRAGMA only) · row 무변경 → 지문/counts 자명 불변")
    n_cx = section_a(conn)
    section_b(conn)
    section_c(conn, n_cx)
    section_d(conn)
    section_e(conn, n_cx)
    section_f(conn)
    section_g(conn, n_cx)
    print("\n# end profile_consistency")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
