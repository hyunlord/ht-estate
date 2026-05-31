"""gym 시드 로더 — data/seeds/gym_gangnam.jsonl → enrichment(store.write_facts).

C4: CC가 에이전트로 추출한 정적 시드를 enrichment 테이블에 적재. 멱등(write_facts upsert)·
재개 가능(fresh 있으면 skip). 키 불필요. value는 {has_gym, evidence} JSON(설계 §4).

    uv run python scripts/load_gym_seed.py                 # 기본 시드·기본 DB
    uv run python scripts/load_gym_seed.py --seed X --db Y --ttl-days 90

시드 레코드: complex_id · has_gym(yes|no|unknown) · in_complex · evidence(요지) ·
confidence · source_type · source_url. complex_id는 K-apt kaptCode(complex FK).

C5에서 공용 코어(_seedlib)로 일반화 — 적재 로직은 공유하되 value 빌더(_to_fact)만 gym 전용.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from _seedlib import load_seed as _load_seed
from _seedlib import read_records

from app.enrich.store import EnrichmentFact
from app.store.db import DEFAULT_DB_PATH, get_connection, init_db

ATTRIBUTE = "gym"
DEFAULT_SEED = Path(__file__).resolve().parents[1] / "data" / "seeds" / "gym_gangnam.jsonl"

# 공용 코어 read_records 별칭(기존 공개 API 유지 — 호출부·테스트 호환).
load_seed_records = read_records


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
    """gym 시드를 enrichment에 멱등 적재(공용 코어 위임). {loaded, skipped, complexes}."""
    return _load_seed(conn, records, attribute=ATTRIBUTE, to_fact=_to_fact, ttl=ttl, now=now)


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
