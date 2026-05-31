"""시드 로더 공용 코어 — 속성 일반화(C5).

C4 gym 로더에서 공통 부분(jsonl 읽기 + 멱등·재개 적재)을 뽑아 속성 무관하게 재사용한다.
속성별 차이는 `attribute`와 `to_fact`(레코드→EnrichmentFact, value JSON 빌더)로 주입한다.
gym(load_gym_seed)·pet(load_pet_seed)이 이 코어를 공유 — 회귀 0(각 로더 테스트가 가드).

멱등: write_facts upsert(PK 충돌). 재개: 단지에 fresh 사실 있으면 skip(has_fresh).
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from app.enrich.store import EnrichmentFact, has_fresh, write_facts

ToFact = Callable[[dict[str, object]], EnrichmentFact]


def read_records(path: Path) -> list[dict[str, object]]:
    """jsonl 시드 → 레코드 리스트(빈 줄 skip)."""
    records: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def load_seed(
    conn: sqlite3.Connection,
    records: list[dict[str, object]],
    *,
    attribute: str,
    to_fact: ToFact,
    ttl: timedelta,
    now: datetime,
) -> dict[str, int]:
    """레코드를 enrichment에 멱등 적재. fresh 있는 단지는 skip(재개). {loaded, skipped, complexes}.

    같은 complex_id의 여러 출처는 묶어서 한 번에 write(출처별 다중 행, store §4).
    """
    by_complex: dict[str, list[EnrichmentFact]] = defaultdict(list)
    for record in records:
        by_complex[str(record["complex_id"])].append(to_fact(record))

    loaded = 0
    skipped = 0
    for complex_id, facts in by_complex.items():
        if has_fresh(conn, complex_id, attribute, now=now):
            skipped += 1
            continue
        loaded += write_facts(conn, complex_id, attribute, facts, ttl=ttl, now=now)
    return {"loaded": loaded, "skipped": skipped, "complexes": len(by_complex)}
