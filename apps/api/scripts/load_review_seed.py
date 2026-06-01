"""review_summary 시드 로더 — data/seeds/review_gangnam.jsonl → enrichment(write_facts).

P3-1: 단지 후기/평판을 **요약+출처**로 적재(표시 전용·랭킹 신호 아님). gym(C4)·pet(C5)과 같은
(a) 경로·공용 코어(_seedlib) 공유하되 review 고유: 상태가 아닌 **요약 텍스트 + 핵심 포인트**.
저작권: value에 짧은 자기표현 요약만(파서가 길이 캡으로 원문 재현 방지). 멱등·재개. 키 불필요.

    uv run python scripts/load_review_seed.py
    uv run python scripts/load_review_seed.py --seed X --db Y --ttl-days 90

시드 레코드: complex_id · summary(짧은 요약) · points(핵심 list) · confidence(보수적) ·
source_type · source_url. value=JSON{summary, points}(§4 — 출처별 다중 행).
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

ATTRIBUTE = "review_summary"
DEFAULT_SEED = Path(__file__).resolve().parents[1] / "data" / "seeds" / "review_gangnam.jsonl"

# 공용 코어 read_records 별칭(gym/pet 로더와 동일 패턴 — 호출부·테스트 호환).
load_seed_records = read_records


def _to_fact(record: dict[str, object]) -> EnrichmentFact:
    """시드 레코드 → EnrichmentFact. value=JSON{summary, points}(표시용 — 저작권: 짧은 요약만)."""
    raw_points = record.get("points")
    points = [str(p) for p in raw_points] if isinstance(raw_points, list) else []
    value = json.dumps(
        {"summary": str(record.get("summary", "")), "points": points},
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
    """review 시드를 enrichment에 멱등 적재(공용 코어 위임). {loaded, skipped, complexes}."""
    return _load_seed(conn, records, attribute=ATTRIBUTE, to_fact=_to_fact, ttl=ttl, now=now)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="load_review_seed", description="review 시드 적재기")
    parser.add_argument("--seed", default=str(DEFAULT_SEED), help="시드 jsonl 경로")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite 경로")
    parser.add_argument("--ttl-days", type=int, default=90, help="enrichment TTL(일, 기본 분기)")
    args = parser.parse_args(argv)

    conn = get_connection(args.db)
    init_db(conn)
    records = load_seed_records(Path(args.seed))
    stats = load_seed(conn, records, ttl=timedelta(days=args.ttl_days), now=datetime.now(UTC))
    print(f"review 시드 — {stats['loaded']} facts · {stats['skipped']} skip(fresh) · "
          f"{stats['complexes']} 단지")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
