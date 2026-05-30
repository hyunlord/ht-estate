"""강남 실데이터 recall 측정 — 지번 매칭 전/후 + 손실 분해 (T0-4c 핵심 산출물).

API 키 필요(.env `DATA_GO_KR_API_KEY`)라 **키리스 게이트 밖**이다. 수동 실행해
리턴팩 증거(before/after·손실분해·회수쌍)를 만든다. 이름 path는 불변이므로
use_jibun False/True 두 패스의 차이가 곧 지번 회수분이다.

    uv run python scripts/recall_report.py                      # 강남구 11680, 기본 월
    uv run python scripts/recall_report.py 11680 202602 202603  # 사용자 지정

K-apt는 단지 목록(name·bjd_code) + 단지별 basis(kaptAddr=지번주소)만 적재한다 —
조인이 쓰는 필드(name·bjd_code·legal_addr)에 한정(주차·헬스장 등 무관해 detail 생략).
"""

from __future__ import annotations

import sqlite3
import sys

from app.settings import get_api_key
from app.sources import kapt
from app.sources._http import fetch_text, json_body
from app.store.db import get_connection, init_db
from app.store.join_repo import backfill_matches, recall_breakdown
from app.store.transaction_repo import ingest_month

DEFAULT_SIGUNGU = "11680"  # 강남구
DEFAULT_MONTHS = ["202602", "202603", "202604"]


def _seed_complexes(conn: sqlite3.Connection, api_key: str, sigungu: str) -> int:
    """단지 목록 + basis(kaptAddr) 적재. 조인이 읽는 4필드만 채운다. 적재 수 반환."""
    refs = kapt.list_complexes(api_key=api_key, sigungu=sigungu)
    for ref in refs:
        body = fetch_text(kapt.BASIS_URL, {"serviceKey": api_key, "kaptCode": ref.kapt_code})
        item = json_body(body).get("item") or {}
        legal_addr = item.get("kaptAddr") if isinstance(item, dict) else None
        conn.execute(
            "INSERT OR REPLACE INTO complex (complex_id, name, bjd_code, legal_addr) "
            "VALUES (?, ?, ?, ?)",
            (ref.kapt_code, ref.name, ref.bjd_code, legal_addr),
        )
    conn.commit()
    return len(refs)


def _matched_ids(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        'SELECT txn_id FROM "transaction" WHERE complex_id IS NOT NULL'
    ).fetchall()
    return {r["txn_id"] for r in rows}


def _reset_matches(conn: sqlite3.Connection) -> None:
    conn.execute('UPDATE "transaction" SET complex_id = NULL, match_confidence = NULL')
    conn.commit()


def _pct(n: int, d: int) -> str:
    return f"{(100.0 * n / d):.1f}%" if d else "n/a"


def main(argv: list[str]) -> None:
    sigungu = argv[1] if len(argv) > 1 else DEFAULT_SIGUNGU
    months = argv[2:] if len(argv) > 2 else DEFAULT_MONTHS
    api_key = get_api_key()

    conn = get_connection(":memory:")
    init_db(conn)
    n_complex = _seed_complexes(conn, api_key, sigungu)
    for ym in months:
        ingest_month(conn, sigungu, ym, api_key=api_key)
    total = conn.execute('SELECT COUNT(*) AS c FROM "transaction"').fetchone()["c"]

    # 패스 A: 이름만 (T0-4b 베이스라인)
    name_only = backfill_matches(conn, use_jibun=False)["matched"]
    name_ids = _matched_ids(conn)
    bd = recall_breakdown(conn)  # 잔여 미매치 손실 분해

    # 패스 B: 지번 포함 (리셋 후 동일 데이터에 재매칭)
    _reset_matches(conn)
    with_jibun = backfill_matches(conn, use_jibun=True)["matched"]
    jibun_ids = _matched_ids(conn)
    gained = sorted(jibun_ids - name_ids)

    print(f"=== T0-4c recall — 시군구 {sigungu} · 월 {','.join(months)} ===")
    print(f"단지(K-apt) {n_complex} · 거래(MOLIT, dedup) {total}")
    print(f"이름만        : {name_only:4d} / {total}  recall {_pct(name_only, total)}")
    print(f"지번 포함     : {with_jibun:4d} / {total}  recall {_pct(with_jibun, total)}")
    print(f"지번 회수분   : +{len(gained)}  (recall +{_pct(with_jibun - name_only, total)})")
    print("--- 잔여 손실 분해(이름만 매칭 후) ---")
    print(f"  지번-회수 가능 : {bd['jibun_recoverable']}")
    print(f"  이름가드 차단  : {bd['name_blocked']}  (단일 점유나 이름 타당성 미달)")
    print(f"  지번 충돌      : {bd['collision']}")
    print(f"  구조적(천장)   : {bd['structural']}  (K-apt에 해당 지번 단지 없음)")
    print(f"  지번 없음      : {bd['no_jibun']}")
    print(f"  미매치 합계    : {bd['total_unmatched']}")
    print("--- 지번 회수쌍(오매칭 점검 — 거래명 → 단지명 [지번] conf) ---")
    for txn_id in gained:
        row = conn.execute(
            'SELECT t.apt_name_raw, t.jibun, c.name AS cname, t.match_confidence AS conf '
            'FROM "transaction" t JOIN complex c ON t.complex_id = c.complex_id '
            "WHERE t.txn_id = ?",
            (txn_id,),
        ).fetchone()
        print(f"  {row['apt_name_raw']} → {row['cname']}  [{row['jibun']}] {row['conf']}")


if __name__ == "__main__":
    main(sys.argv)
