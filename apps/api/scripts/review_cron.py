"""review 후기 cron — **enrich_cron(일반화)의 back-compat shim**.

enrich-cron-gate에서 cron을 attribute-parameterized `enrich_cron.py`로 일반화했다. review 경로·CLI·
테스트 호환을 위해 이 모듈은 enrich_cron으로 위임한다(staging+gate 동일: DB writer 미import).
신규 사용은 `enrich_cron.py --attribute review` 권장. cron 가이드는 docs/auto-enrich.md.

  uv run python scripts/review_cron.py --limit 20            # = enrich_cron --attribute review
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import _bootstrap  # noqa: F401  (side-effect: apps/api를 sys.path에)

# 일반화 모듈로 위임 — DB writer는 여기서도 import 안 함(게이트 보존).
from enrich_cron import (
    ClaudeRunner,
    _default_runner,
    append_staging,
    read_staging,
    staged_complex_ids,
    staged_pairs,
    staging_path,
)
from enrich_cron import (
    run_cron as _run_cron,
)

from app.store.db import DEFAULT_DB_PATH, get_connection, init_db

__all__ = [
    "REVIEW_ATTRIBUTE", "DEFAULT_STAGING", "read_staging", "staged_complex_ids",
    "staged_pairs", "append_staging", "run_cron", "main",
]

REVIEW_ATTRIBUTE = "review_summary"
DEFAULT_STAGING = staging_path("review")  # data/staging/review.jsonl(gitignored)


def run_cron(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    limit: int,
    max_turns: int,
    runner: ClaudeRunner = _default_runner,
    staging_path: Path = DEFAULT_STAGING,
) -> dict[str, int]:
    """review cron 1배치(enrich_cron 위임, attr='review'). staging-only — DB write 0."""
    return _run_cron(
        conn, "review", now=now, limit=limit, max_turns=max_turns,
        runner=runner, staging_path_override=staging_path,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="review_cron",
        description="review 후기 cron(= enrich_cron --attribute review) — staging까지만.",
    )
    parser.add_argument("--limit", type=int, default=20, help="이번 run 단지 수(저volume 권장)")
    parser.add_argument("--max-turns", type=int, default=80, help="claude -p turn 상한")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite 경로(읽기전용)")
    parser.add_argument("--staging", default=str(DEFAULT_STAGING), help="staging JSONL 경로")
    args = parser.parse_args(argv)

    conn = get_connection(args.db)
    init_db(conn)
    stats = run_cron(
        conn, now=datetime.now(UTC), limit=args.limit,
        max_turns=args.max_turns, staging_path=Path(args.staging),
    )
    print(
        f"[review-cron] 선택 {stats['selected']} · 추출 {stats['extracted']} · "
        f"staging {stats['staged']} → {args.staging}"
    )
    print("※ staging까지만 — spot-audit 후 promote 절차는 docs/review-cron.md 참고.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
