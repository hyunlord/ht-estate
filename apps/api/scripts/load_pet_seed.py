"""pet_allowed 시드 로더 — data/seeds/pet_gangnam.jsonl → enrichment(store.write_facts).

C5: CC가 에이전트로 추출한 정적 pet 시드를 적재. gym(C4)과 같은 (a) 경로지만 설계의
"가장 약한 고리"(§6·§11)라 데이터 규율이 더 엄격(보수적 confidence·관리사무소 확인 권고·
견종/무게 caveats·잘못된 "가능" 금지). 멱등·재개. 키 불필요. 공용 코어(_seedlib) 공유.

    uv run python scripts/load_pet_seed.py                 # 기본 시드·기본 DB
    uv run python scripts/load_pet_seed.py --seed X --db Y --ttl-days 90

시드 레코드: complex_id · pet_allowed(yes|conditional|no|unknown) · evidence(요지) ·
caveats(list — 견종/무게/마릿수 등 제한) · confidence(보수적) · confirm_with_office(항상 true) ·
source_type · source_url. value=JSON{pet_allowed, evidence, caveats, confirm_with_office}(§4).
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

ATTRIBUTE = "pet_allowed"
DEFAULT_SEED = Path(__file__).resolve().parents[1] / "data" / "seeds" / "pet_gangnam.jsonl"

# 공용 코어 read_records 별칭(gym 로더와 동일 패턴 — 호출부·테스트 호환).
load_seed_records = read_records


def _to_fact(record: dict[str, object]) -> EnrichmentFact:
    """시드 레코드 → EnrichmentFact. value=JSON{pet_allowed, evidence, caveats, confirm_office}.

    confirm_with_office는 항상 보존(없으면 보수적 true) — 카드가 '관리사무소 확인' 권고.
    """
    value = json.dumps(
        {
            "pet_allowed": record["pet_allowed"],
            "evidence": record.get("evidence", ""),
            "caveats": record.get("caveats", []),
            "confirm_with_office": record.get("confirm_with_office", True),
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
    """pet 시드를 enrichment에 멱등 적재(공용 코어 위임). {loaded, skipped, complexes}."""
    return _load_seed(conn, records, attribute=ATTRIBUTE, to_fact=_to_fact, ttl=ttl, now=now)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="load_pet_seed", description="pet_allowed 시드 적재기")
    parser.add_argument("--seed", default=str(DEFAULT_SEED), help="시드 jsonl 경로")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite 경로")
    parser.add_argument("--ttl-days", type=int, default=90, help="enrichment TTL(일, 기본 분기)")
    args = parser.parse_args(argv)

    conn = get_connection(args.db)
    init_db(conn)
    records = load_seed_records(Path(args.seed))
    stats = load_seed(conn, records, ttl=timedelta(days=args.ttl_days), now=datetime.now(UTC))
    print(f"pet 시드 적재 — {stats['loaded']} facts 적재 · {stats['skipped']} 단지 skip(fresh) · "
          f"총 {stats['complexes']} 단지")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
