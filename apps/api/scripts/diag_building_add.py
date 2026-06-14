"""거래-도출 아파트 건물 사이징 — #6-③B-1 (read-only recon).

post-join complex_id NULL 아파트 거래(매매+전월세)를 nonapt식 정규화 building_key로 묶어
B-2(대량 생성+지오코딩+re-baseline) 규모를 측정한다. **SELECT만**(대장/Kakao API 콜 0·write 0)
→ 지문/counts 자명 불변. 기존 nonapt_repo.building_key·match.jibun·regions 정규화 재사용.

리포트: 신규 건물 수(매매/전월세/합) · counts 델타 · 회수될 거래 수 · 지오코딩 부하 ·
dup 위험 그룹 · 도출불가 잔여 · sido top-10.

    uv run python scripts/diag_building_add.py
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from types import SimpleNamespace

import _bootstrap  # noqa: F401

from app.match.jibun import from_molit, to_canonical
from app.match.normalize import normalize_name
from app.store.db import DEFAULT_DB_PATH, get_connection
from app.store.nonapt_repo import building_key
from app.store.regions import canonical_sido, region_by_code

TABLES = [("매매(transaction)", "transaction"), ("전월세(rent_transaction)", "rent_transaction")]


def _derivable(row) -> bool:
    """building_key가 비-degenerate(jibun·name 둘 다 산출 가능)인가 — 도출 가능 orphan."""
    if not (row["apt_name_raw"] and normalize_name(row["apt_name_raw"])):
        return False
    if not (row["bjd_code"] and len(row["bjd_code"]) >= 5):
        return False
    return to_canonical(from_molit(None, None, row["jibun"])) is not None


def _key(row) -> str:
    """orphan 거래 → nonapt식 building_key(아파트 prop·sgg=bjd[:5]). building_key 재사용."""
    shim = SimpleNamespace(
        property_type="apartment",
        sgg_cd=row["bjd_code"][:5],
        legal_dong=row["legal_dong"] or "",
        jibun=row["jibun"],
        name=row["apt_name_raw"],
    )
    return building_key(shim)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="diag_building_add")
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    args = ap.parse_args(argv)
    conn = get_connection(args.db)
    print(f"# diag_building_add — db={args.db}  (read-only·API 0)")

    key_trades: dict[str, int] = Counter()       # building_key → 묶이는 orphan 거래 수(합산)
    per_table_keys: dict[str, set[str]] = {}      # 테이블 → distinct key 집합
    per_table_cov = {}                            # 테이블 → 도출가능 orphan 거래 수
    nonderiv = {}                                 # 테이블 → 도출불가 orphan 수
    # dup 위험: (sgg, 지번) → 갈리는 정규화 건물명 집합.
    site_names: dict[tuple[str, str], set[str]] = defaultdict(set)

    for label, table in TABLES:
        rows = conn.execute(
            f'SELECT apt_name_raw, bjd_code, legal_dong, jibun FROM "{table}" '
            "WHERE complex_id IS NULL"
        ).fetchall()
        keys: set[str] = set()
        cov = 0
        nd = 0
        for r in rows:
            if not _derivable(r):
                nd += 1
                continue
            k = _key(r)
            keys.add(k)
            key_trades[k] += 1
            cov += 1
            jibun_c = to_canonical(from_molit(None, None, r["jibun"]))
            site_names[(r["bjd_code"][:5], jibun_c)].add(normalize_name(r["apt_name_raw"]))
        per_table_keys[label] = keys
        per_table_cov[label] = cov
        nonderiv[label] = nd
        print(f"\n══ {label} — orphan {len(rows):,} ══")
        print(f"    신규 건물(distinct key) = {len(keys):,}")
        pct = 100.0 * cov / (len(rows) or 1)
        print(f"    도출가능 orphan 거래(회수폭) = {cov:,}  ({pct:.1f}%)")
        print(f"    도출불가 잔여(jibun/name/bjd 결손) = {nd:,}")

    union_keys = set().union(*per_table_keys.values())
    print("\n══ 합산(매매 ∪ 전월세) ══")
    print(f"    신규 건물 distinct(union) = {len(union_keys):,}  "
          f"→ counts 델타 complex 172,879 → +{len(union_keys):,}")
    print(f"    회수될 거래 합 = 매매 {per_table_cov['매매(transaction)']:,} + "
          f"전월세 {per_table_cov['전월세(rent_transaction)']:,} = "
          f"{sum(per_table_cov.values()):,}")
    print(f"    지오코딩 부하(신규 건물당 1 Kakao 콜) ≈ {len(union_keys):,} 콜")

    dup_groups = sum(1 for names in site_names.values() if len(names) > 1)
    print(f"    dup 위험((sgg,지번)인데 건물명 갈림) 그룹 = {dup_groups:,}")

    # sido 분포 — 신규 건물 key의 sgg(2번째 토큰)→canonical sido.
    sido_ct: Counter = Counter()
    for k in union_keys:
        sgg = k.split(":")[1]
        reg = region_by_code(sgg)
        sido = reg[0] if reg else canonical_sido(sgg)
        sido_ct[sido or "(미상)"] += 1
    print("\n    신규 건물 sido top-10:")
    for sido, n in sido_ct.most_common(10):
        print(f"      {sido:<14} {n:>8,}")

    print("\n# end diag_building_add")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
