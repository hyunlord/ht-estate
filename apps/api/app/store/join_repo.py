"""실거래↔단지 퍼지 조인 백필 (설계 §5.1).

transaction.complex_id가 NULL인 행을 법정동으로 후보를 좁혀 단지명 유사도로 매칭,
complex_id·match_confidence를 채운다. **조인 컬럼만 갱신**(apt_name_raw 등 불변).
멱등: 이미 매칭(complex_id NOT NULL)된 행은 건드리지 않아 재실행 결과가 같다.
T0-3 upsert가 재적재 시 조인 컬럼을 보존하므로 적재와 백필은 독립이다.

aptSeq↔kaptCode 직접 링크는 불가(라이브 확정 — 다른 ID 체계)라 퍼지가 유일 경로.
narrowing은 **법정동코드(bjd_code) 동등**을 우선한다(T0-4b — MOLIT sggCd+umdCd =
K-apt bjdCode, 동 표기변이·legal_addr 파싱 fragility에 안 깨짐). bjd_code 없는 행만
동 이름으로 fallback. 정밀도(임계값)는 불변 — narrowing만 결정론화한다.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from app.match.fuzzy import DEFAULT_AMBIGUITY_GAP, DEFAULT_THRESHOLD, best_match
from app.match.normalize import extract_dong


def _complex_dong(row: sqlite3.Row) -> str | None:
    """단지 행의 법정동. dong 컬럼 우선, 없으면 legal_addr에서 추출."""
    return row["dong"] or extract_dong(row["legal_addr"])


def backfill_matches(
    conn: sqlite3.Connection,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    ambiguity_gap: float = DEFAULT_AMBIGUITY_GAP,
) -> dict[str, int]:
    """complex_id NULL인 거래를 일괄 매칭. {matched, unmatched, total} 반환. 멱등.

    무매치/모호는 complex_id를 NULL로 남긴다(억지매칭 금지). 매칭된 행에만
    match_confidence를 채운다.
    """
    # narrowing 인덱스 2개: 법정동코드(결정론, 우선) + 동 이름(코드 없는 행 fallback).
    by_bjd: dict[str, list[tuple[str, str]]] = defaultdict(list)
    by_dong: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for c in conn.execute("SELECT complex_id, name, bjd_code, dong, legal_addr FROM complex"):
        if not c["name"]:
            continue
        entry = (c["complex_id"], c["name"])
        if c["bjd_code"]:
            by_bjd[c["bjd_code"]].append(entry)
        dong = _complex_dong(c)
        if dong:
            by_dong[dong].append(entry)

    pending = conn.execute(
        'SELECT txn_id, apt_name_raw, legal_dong, bjd_code FROM "transaction" '
        "WHERE complex_id IS NULL"
    ).fetchall()

    matched = 0
    for txn in pending:
        if not txn["apt_name_raw"]:
            continue
        # bjd_code가 있으면 법정동코드 동등으로 narrowing(동 표기변이에 안 깨짐).
        # 없으면 동 이름 fallback.
        if txn["bjd_code"]:
            candidates = by_bjd.get(txn["bjd_code"], [])
        elif txn["legal_dong"]:
            candidates = by_dong.get(txn["legal_dong"], [])
        else:
            continue
        result = best_match(
            txn["apt_name_raw"], candidates, threshold=threshold, ambiguity_gap=ambiguity_gap
        )
        if result is None:
            continue
        complex_id, score = result
        conn.execute(
            'UPDATE "transaction" SET complex_id = ?, match_confidence = ? WHERE txn_id = ?',
            (complex_id, score, txn["txn_id"]),
        )
        matched += 1

    conn.commit()
    return {"matched": matched, "unmatched": len(pending) - matched, "total": len(pending)}
