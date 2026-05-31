"""gym 시드 로더 — data/seeds/gym_gangnam.jsonl → enrichment(store.write_facts).

C4: CC가 에이전트로 추출한 정적 시드를 enrichment 테이블에 적재. 멱등(write_facts upsert)·
재개 가능(fresh 있으면 skip). 키 불필요. value는 {has_gym, evidence} JSON(설계 §4).

    uv run python scripts/load_gym_seed.py                 # 기본 시드·기본 DB
    uv run python scripts/load_gym_seed.py --seed X --db Y --ttl-days 90

시드 레코드: complex_id · has_gym(yes|no|unknown) · in_complex · evidence(요지) ·
confidence · source_type · source_url. complex_id는 K-apt kaptCode(complex FK).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.enrich.store import EnrichmentFact, has_fresh, write_facts
from app.store.db import DEFAULT_DB_PATH, get_connection, init_db

ATTRIBUTE = "gym"
DEFAULT_SEED = Path(__file__).resolve().parents[1] / "data" / "seeds" / "gym_gangnam.jsonl"


def load_seed_records(path: Path) -> list[dict[str, object]]:
    """jsonl 시드 → 레코드 리스트(빈 줄 skip)."""
    records: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def _to_fact(record: dict[str, object]) -> EnrichmentFact:
    """시드 레코드 → EnrichmentFact. value=JSON{has_gym, evidence}."""
    value = json.dumps(
        {"has_gym": record["has_gym"], "evidence": record.get("evidence", "")},
        ensure_ascii=False,
    )
    return EnrichmentFact(
        value=value,
        confidence=float(record["confidence"]),  # type: ignore[arg-type]
        source_type=str(record["source_type"]),
        source_url=str(record["source_url"]),
    )


def load_seed(
    conn: sqlite3.Connection,
    records: list[dict[str, object]],
    *,
    ttl: timedelta,
    now: datetime,
) -> dict[str, int]:
    """시드를 enrichment에 멱등 적재. fresh 있는 단지는 skip(재개). {loaded, skipped, complexes}."""
    by_complex: dict[str, list[EnrichmentFact]] = defaultdict(list)
    for record in records:
        by_complex[str(record["complex_id"])].append(_to_fact(record))

    loaded = 0
    skipped = 0
    for complex_id, facts in by_complex.items():
        if has_fresh(conn, complex_id, ATTRIBUTE, now=now):
            skipped += 1
            continue
        loaded += write_facts(conn, complex_id, ATTRIBUTE, facts, ttl=ttl, now=now)
    return {"loaded": loaded, "skipped": skipped, "complexes": len(by_complex)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="load_gym_seed", description="gym 시드 적재기")
    parser.add_argument("--seed", default=str(DEFAULT_SEED), help="시드 jsonl 경로")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite 경로")
    parser.add_argument("--ttl-days", type=int, default=90, help="enrichment TTL(일, 기본 분기)")
    args = parser.parse_args(argv)

    conn = get_connection(args.db)
    init_db(conn)
    records = load_seed_records(Path(args.seed))
    stats = load_seed(conn, records, ttl=timedelta(days=args.ttl_days), now=datetime.now(UTC))
    print(f"gym 시드 적재 — {stats['loaded']} facts 적재 · {stats['skipped']} 단지 skip(fresh) · "
          f"총 {stats['complexes']} 단지")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
