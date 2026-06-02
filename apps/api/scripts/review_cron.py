"""review 후기 cron 골격 — 저volume + **human commit gate** (review-cron 티켓).

review 라이브 품질 게이트 GO 후, 후기 실수집을 cron으로 골격화한다. **자동은 staging까지만**:
미적재 후보를 `claude -p`(구독, WebSearch/WebFetch)로 추출 → `parse_review_output`(규율 강제) →
**staging JSONL append**. 라이브 DB write·git commit은 **안 한다**(사람 spot-audit 후 promote).

설계 정합:
- review는 **표시 전용**(랭킹 신호 아님 — SoftSpec=={gym,pet}). cron이 이를 바꾸지 않는다.
- **human commit gate(코드강제)**: 모듈이 DB writer(load_seed/write_facts)를 **import 안 함**.
  DB 접근은 select(읽기 전용)뿐. staging은 gitignore된 `data/staging/`라 자동 git 진입 불가.
- **멱등·재개**: select가 DB-fresh(has_fresh) + 이미 staging된 단지(exclude_ids)를 제외.
  중간에 끊겨도 다음 run이 fresh/staging skip으로 이어간다(재질의·중복 추출 방지).
- **dedup**: parse가 (complex_id, source_url) dedup, append_staging이 staging 누적분과 재dedup.

promote(사람 단계, 이 모듈 밖):
  1) `data/staging/review.jsonl` spot-audit(summary↔source 대조).
  2) 검증된 줄을 `data/seeds/review_gangnam.jsonl`로 옮기고 git commit(사람 리뷰 후).
  3) `uv run python scripts/load_review_seed.py`로 DB 적재(라이브 write — 사람 트리거).
자세한 절차·cron 예시는 docs/review-cron.md.

  uv run python scripts/review_cron.py --limit 20 --max-turns 80
  uv run python scripts/review_cron.py --db /path/ht.db --staging /path/review.jsonl --limit 10
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import _bootstrap  # noqa: F401  (side-effect: apps/api를 sys.path에 — PYTHONPATH 불필요)

# auto_enrich의 building block 재사용 — **읽기/추출/파싱만**(DB writer는 의도적으로 import 안 함).
from auto_enrich import (
    ClaudeRunner,
    _default_runner,
    build_prompt,
    parse_review_output,
    select_candidates,
)

from app.store.db import DEFAULT_DB_PATH, get_connection, init_db

REVIEW_ATTRIBUTE = "review_summary"
REVIEW_PROMPT = "enrich_review.md"
# staging은 gitignore된 위치(data/staging/) — 무검토 자동 출력이 자동으로 추적/적재되지 않게.
DEFAULT_STAGING = Path(__file__).resolve().parents[1] / "data" / "staging" / "review.jsonl"


def read_staging(path: Path) -> list[dict[str, object]]:
    """staging JSONL → 레코드 리스트(없으면 빈 리스트, 빈 줄 skip)."""
    if not path.exists():
        return []
    records: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def staged_complex_ids(path: Path) -> set[str]:
    """이미 staging된 단지 id 집합 — 재개 시 재질의 skip(exclude_ids로 전달)."""
    return {str(r.get("complex_id", "")) for r in read_staging(path) if r.get("complex_id")}


def staged_pairs(path: Path) -> set[tuple[str, str]]:
    """staging의 (complex_id, source_url) 집합 — append dedup 기준(다출처 허용·중복 줄 방지)."""
    return {
        (str(r.get("complex_id", "")), str(r.get("source_url", "")))
        for r in read_staging(path)
    }


def append_staging(path: Path, records: list[dict[str, object]]) -> int:
    """검증된 레코드를 staging JSONL에 append(누적). (complex_id, source_url) dedup. 쓴 줄 수 반환.

    이미 staging에 있는 (단지, 출처)는 건너뛴다(재개·재실행 멱등). 라이브 DB·git은 건드리지 않음.
    """
    if not records:
        return 0
    seen = staged_pairs(path)
    fresh: list[dict[str, object]] = []
    for rec in records:
        key = (str(rec.get("complex_id", "")), str(rec.get("source_url", "")))
        if key in seen:
            continue
        seen.add(key)
        fresh.append(rec)
    if not fresh:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for rec in fresh:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(fresh)


def run_cron(
    conn: sqlite3.Connection,  # 읽기 전용 — select만(DB write 없음)
    *,
    now: datetime,
    limit: int,
    max_turns: int,
    runner: ClaudeRunner = _default_runner,
    staging_path: Path = DEFAULT_STAGING,
) -> dict[str, int]:
    """review cron 1배치: select(fresh+staging 제외)→claude→parse→**staging append**. DB write 0.

    멱등·재개: DB-fresh + 이미 staging된 단지를 둘 다 제외해 재질의/중복 추출을 막는다.
    무후보면 즉시 0 반환(claude 미호출). {selected, extracted, staged} 반환.
    """
    excluded = staged_complex_ids(staging_path)
    candidates = select_candidates(
        conn, REVIEW_ATTRIBUTE, now=now, limit=limit, exclude_ids=excluded
    )
    if not candidates:
        return {"selected": 0, "extracted": 0, "staged": 0}

    prompt = build_prompt(REVIEW_PROMPT, candidates)
    output = runner(prompt, max_turns)
    valid_ids = {c["complex_id"] for c in candidates}
    records = parse_review_output(output, valid_ids)
    staged = append_staging(staging_path, records)
    return {"selected": len(candidates), "extracted": len(records), "staged": staged}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="review_cron",
        description="review 후기 cron — staging까지만(human commit gate). DB·git write 안 함.",
    )
    parser.add_argument("--limit", type=int, default=20, help="이번 run 단지 수(저volume 권장)")
    parser.add_argument("--max-turns", type=int, default=80, help="claude -p turn 상한")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite 경로(읽기전용)")
    parser.add_argument("--staging", default=str(DEFAULT_STAGING), help="staging JSONL 경로")
    args = parser.parse_args(argv)

    conn = get_connection(args.db)
    init_db(conn)
    stats = run_cron(
        conn,
        now=datetime.now(UTC),
        limit=args.limit,
        max_turns=args.max_turns,
        staging_path=Path(args.staging),
    )
    print(
        f"[review-cron] 선택 {stats['selected']} · 추출 {stats['extracted']} · "
        f"staging {stats['staged']} → {args.staging}"
    )
    print("※ staging까지만 — spot-audit 후 promote 절차는 docs/review-cron.md 참고.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
