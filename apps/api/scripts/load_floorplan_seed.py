"""floorplan 시드 로더 — data/seeds/floorplan_gangnam.jsonl → enrichment(write_facts).

P3-2: 평면도 VLM **객관 feature**(bay·향·판상/타워)를 적재(표시 전용·랭킹 아님). gym/pet/review와
같은 (a) 경로·공용 코어(_seedlib) 공유. value=JSON{bay, orientation, structure, evidence}.
§11: feature-only(점수화 금지) — 파서(parse_floorplan_output)가 도메인·null 규율을 이미 강제한다.
멱등·재개. 키 불필요(파서·로더는 키리스; 실 LH 적재는 사용자 ops `floorplan_poc.py --run`).

    uv run python scripts/load_floorplan_seed.py
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import _bootstrap  # noqa: F401  (side-effect: apps/api를 sys.path에 — PYTHONPATH 불필요)
from _seedlib import load_seed as _load_seed
from _seedlib import read_records

from app.enrich.store import EnrichmentFact
from app.store.db import DEFAULT_DB_PATH, get_connection, init_db

ATTRIBUTE = "floorplan"
DEFAULT_SEED = Path(__file__).resolve().parents[1] / "data" / "seeds" / "floorplan_gangnam.jsonl"

# 공용 코어 read_records 별칭(gym/pet/review 로더와 동일 패턴 — 호출부·테스트 호환).
load_seed_records = read_records


def _to_fact(record: dict[str, object]) -> EnrichmentFact:
    """시드 레코드 → EnrichmentFact. value=JSON{bay,orientation,structure,evidence}."""
    value = json.dumps(
        {
            "bay": record.get("bay"),
            "orientation": record.get("orientation"),
            "structure": record.get("structure"),
            "evidence": str(record.get("evidence", "")),
        },
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
    """floorplan 시드를 enrichment에 멱등 적재(공용 코어 위임). {loaded, skipped, complexes}."""
    return _load_seed(conn, records, attribute=ATTRIBUTE, to_fact=_to_fact, ttl=ttl, now=now)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="load_floorplan_seed", description="floorplan 시드 적재")
    parser.add_argument("--seed", default=str(DEFAULT_SEED), help="시드 jsonl 경로")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite 경로")
    parser.add_argument("--ttl-days", type=int, default=90, help="enrichment TTL(일, 기본 분기)")
    args = parser.parse_args(argv)

    conn = get_connection(args.db)
    init_db(conn)
    records = load_seed_records(Path(args.seed))
    stats = load_seed(conn, records, ttl=timedelta(days=args.ttl_days), now=datetime.now(UTC))
    print(f"floorplan 시드 — {stats['loaded']} facts · {stats['skipped']} skip(fresh) · "
          f"{stats['complexes']} 단지")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
