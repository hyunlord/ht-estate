"""표본 gym/pet 라이브 추출 + 후보별 fact 출력 (E1-live PART4) — 짓고-돌려보고-품질확인.

후보 소표본(gym=비아파트 포함, pet=몇 개)을 **로컬 Gemma(provider_from_env) + Naver(naver_fetcher_
from_env)**로 라이브 추출해 enrichment에 write-back하고, 후보별 fact(state·confidence·source_url·
source_type·caveats·confirm_with_office)를 출력한다. **후보 한정·bounded 병렬(기본 2)·graceful**.

키 필요(.env: ENRICH_LLM_*·NAVER_*)라 **키리스 게이트 밖**(수동 ops 스크립트). enrichment 테이블만
write → 지문·건물/거래 수 불변. 검색경로 무배선(보류 — 동기 22s/후보, PART4 품질 후 별도 설계).

    uv run python scripts/enrich_gym_pet_sample.py             # 자동 표본(gym 3 비아파트 + pet 2)
    uv run python scripts/enrich_gym_pet_sample.py --gym 4 --pet 3
    uv run python scripts/enrich_gym_pet_sample.py --gym-ids of:11710:... --pet-ids ...
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import _bootstrap  # noqa: F401  (apps/api를 sys.path에)

import app.settings  # noqa: F401  (루트 .env 로딩 — provider/fetcher env 활성화)
from app.enrich.fetcher import naver_fetcher_from_env
from app.enrich.live import live_extractors
from app.enrich.provider import provider_from_env
from app.enrich.runner import enrich
from app.store.db import DEFAULT_DB_PATH, get_connection

TTL = timedelta(days=90)


def _pick(conn: sqlite3.Connection, where: str, n: int) -> list[sqlite3.Row]:
    """name·household 있는 후보를 household desc로 — 웹 노출 큰 단지부터(품질 표본)."""
    return conn.execute(
        f"SELECT complex_id, name, property_type, household_count FROM complex "
        f"WHERE {where} AND name IS NOT NULL AND household_count IS NOT NULL "
        f"ORDER BY household_count DESC LIMIT ?",
        (n,),
    ).fetchall()


def _rows_for(conn: sqlite3.Connection, ids: list[str]) -> list[sqlite3.Row]:
    ph = ",".join("?" * len(ids))
    return conn.execute(
        f"SELECT complex_id, name, property_type, household_count FROM complex "
        f"WHERE complex_id IN ({ph})",
        ids,
    ).fetchall()


def _print_facts(attr: str, rows: list[sqlite3.Row], facts_map: dict) -> None:
    for r in rows:
        cid = r["complex_id"]
        facts = facts_map.get(cid, [])
        head = f"[{attr}] {r['name']} ({cid}, {r['property_type']}, 세대 {r['household_count']})"
        if not facts:
            print(f"{head}\n    · (fact 없음 — defer/miss: 소스 무결과 or provider/parse defer)")
            continue
        print(head)
        for f in facts:
            val = json.loads(f.value)
            state = val.get("has_gym") if attr == "gym" else val.get("pet_allowed")
            line = f"    · state={state} conf={f.confidence:.2f} [{f.source_type}] {f.source_url}"
            print(line)
            if val.get("evidence"):
                print(f"        evidence: {val['evidence']}")
            if attr == "pet":
                print(
                    f"        caveats={val.get('caveats')} "
                    f"confirm_with_office={val.get('confirm_with_office')}"
                )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="enrich_gym_pet_sample")
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--gym", type=int, default=3, help="gym 표본 수(비아파트)")
    ap.add_argument("--pet", type=int, default=2, help="pet 표본 수(아파트)")
    ap.add_argument("--gym-ids", nargs="*", default=None)
    ap.add_argument("--pet-ids", nargs="*", default=None)
    ap.add_argument("--concurrency", type=int, default=2, help="라이브 병렬도(낮게 — Gemma 경합)")
    args = ap.parse_args(argv)

    conn = get_connection(args.db)
    provider = provider_from_env()
    fetcher = naver_fetcher_from_env()
    print(
        f"provider={'OK' if provider else 'NONE'} "
        f"model={getattr(provider, 'model', None)} "
        f"timeout={getattr(provider, 'timeout', None)} "
        f"max_tokens={getattr(provider, 'max_tokens', None)} | "
        f"fetcher={'Naver' if fetcher else 'NULL'} | concurrency={args.concurrency}"
    )
    if provider is None or fetcher is None:
        print("✗ provider/fetcher 미구성 — .env(ENRICH_LLM_*·NAVER_CLIENT_ID/SECRET) 확인. 중단.")
        return 1

    gym_rows = (
        _rows_for(conn, args.gym_ids)
        if args.gym_ids
        else _pick(conn, "property_type != 'apartment'", args.gym)
    )
    pet_rows = (
        _rows_for(conn, args.pet_ids)
        if args.pet_ids
        else _pick(conn, "property_type = 'apartment'", args.pet)
    )

    for attr, rows in (("gym", gym_rows), ("pet", pet_rows)):
        ids = [r["complex_id"] for r in rows]
        if not ids:
            continue
        print(f"\n=== {attr} 라이브 추출 ({len(ids)} 후보) ===")
        t0 = datetime.now(UTC)
        exts = live_extractors(conn, ids, provider=provider, fetcher=fetcher)
        assert exts is not None  # provider 확인됨
        facts_map = enrich(
            conn,
            ids,
            attr,
            exts[attr],
            ttl=TTL,
            now=datetime.now(UTC),
            concurrency=args.concurrency,
        )
        dt = (datetime.now(UTC) - t0).total_seconds()
        _print_facts(attr, rows, facts_map)
        hits = sum(1 for v in facts_map.values() if v)
        print(f"  → {attr}: {hits}/{len(ids)} 후보 fact 획득, {dt:.0f}s")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
