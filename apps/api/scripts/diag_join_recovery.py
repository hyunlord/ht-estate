"""조인 고아 진단 — 미매칭 거래를 실패원인별로 버킷팅 (#6-① S1·read-only).

매매·전월세 각각, complex_id NULL 거래를 bjd/동 narrowing 후 현 matcher로 평가해 분류:
  no-candidate   — 같은 narrowing 키에 후보 단지 0 (구조적·normalize로 회수 불가)
  number-conflict— 번호셋 disjoint로 거절(현대5차≠6차·대체로 정당 거절)
  below-threshold— top < threshold (★회수 가능 후보 — 스코어링 개선 여지)
  ambiguity-tie  — top ≥ threshold인데 gap < ambiguity_gap (모호 → 강제매칭 위험)
  matchable-now  — 현 matcher가 이미 이름 path로 매칭 가능(재실행 회수분)
  no-name        — apt_name_raw 없음

쓰기 없음(SELECT only). 회수 천장 = matchable-now + 안전 일부. geocode/counts 무영향.

    uv run python scripts/diag_join_recovery.py
    uv run python scripts/diag_join_recovery.py --dump 30
"""

from __future__ import annotations

import argparse
from difflib import SequenceMatcher

import _bootstrap  # noqa: F401

from app.match.fuzzy import DEFAULT_AMBIGUITY_GAP, DEFAULT_THRESHOLD, similarity
from app.match.normalize import normalize_name
from app.store.db import DEFAULT_DB_PATH, get_connection
from app.store.join_repo import JOINABLE_TABLES, _Indexes

TABLES = [("매매(transaction)", "transaction"), ("전월세(rent_transaction)", "rent_transaction")]


def _unguarded(name: str, cand: str) -> float:
    """번호가드를 뺀 이름 유사도 — 'guard만 막았나(number-conflict)' 판별용(진단 전용)."""
    q, c = normalize_name(name), normalize_name(cand)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    base = SequenceMatcher(None, q, c).ratio()
    if len(q) >= 2 and q in c:
        base = max(base, 0.9)
    return base


def diagnose(conn, table: str, *, threshold: float, gap: float, dump: int):
    if table not in JOINABLE_TABLES:
        raise ValueError(table)
    idx = _Indexes(conn)
    b = {
        "no-candidate": 0, "number-conflict": 0, "below-threshold": 0,
        "ambiguity-tie": 0, "matchable-now": 0, "no-name": 0, "total": 0,
    }
    examples: list[tuple[str, str, float, float]] = []
    pending = conn.execute(
        f'SELECT apt_name_raw, legal_dong, bjd_code, jibun FROM "{table}" '
        "WHERE complex_id IS NULL"
    ).fetchall()
    b["total"] = len(pending)
    for txn in pending:
        name = txn["apt_name_raw"]
        if not name:
            b["no-name"] += 1
            continue
        cands = idx.candidates(txn)
        if not cands:
            b["no-candidate"] += 1
            continue
        scored = sorted(
            ((similarity(name, cn), cid, cn) for cid, cn in cands),
            key=lambda x: x[0], reverse=True,
        )
        top, _, top_name = scored[0]
        second = scored[1][0] if len(scored) > 1 else 0.0
        if top >= threshold and (top - second) >= gap:
            b["matchable-now"] += 1
        elif top >= threshold:
            b["ambiguity-tie"] += 1
        else:
            ub = max(_unguarded(name, cn) for _, cn in cands)
            if ub >= threshold:
                b["number-conflict"] += 1
            else:
                b["below-threshold"] += 1
                if len(examples) < dump:
                    examples.append((name, top_name, round(top, 3), round(ub, 3)))
    return b, examples


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="diag_join_recovery")
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--dump", type=int, default=20, help="below-threshold 예시 N개 노출")
    args = ap.parse_args(argv)
    conn = get_connection(args.db)
    print(f"# diag_join_recovery — db={args.db}  (read-only)")
    for label, table in TABLES:
        b, ex = diagnose(
            conn, table, threshold=DEFAULT_THRESHOLD, gap=DEFAULT_AMBIGUITY_GAP, dump=args.dump
        )
        tot = b["total"] or 1
        print(f"\n══ {label} — 미매칭 {b['total']:,}건 버킷 ══")
        for k in ["no-candidate", "number-conflict", "below-threshold",
                  "ambiguity-tie", "matchable-now", "no-name"]:
            print(f"    {k:<16} {b[k]:>10,}  {100.0 * b[k] / tot:6.2f}%")
        ceiling = b["matchable-now"] + b["below-threshold"] + b["ambiguity-tie"]
        print(f"    → 회수천장(이름 path 개선 사정권: matchable+below+tie) "
              f"{ceiling:,} ({100.0 * ceiling / tot:.2f}%)")
        if ex:
            print("    below-threshold 예시(거래명 → 최근접 단지 | top / 번호가드제외):")
            for nm, cn, t, u in ex:
                print(f"      {nm[:26]:<26} → {cn[:26]:<26} | {t} / {u}")
    print("\n# end diag")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
