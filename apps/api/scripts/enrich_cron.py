"""enrichment cron — attribute-parameterized 저volume + **human commit gate** (enrich-cron-gate).

review에 적용한 staging+gate 패턴을 gym/pet/review로 일반화한다. **무검토 자동 DB write 0**:
미적재 후보를 `claude -p`(구독, WebSearch/WebFetch)로 추출 → 속성별 파서(규율 강제) →
**`data/staging/<attr>.jsonl`(gitignored) append**. 라이브 DB write·git commit은 **안 한다**
(사람 spot-audit 후 promote). gym/pet은 **랭킹 신호**(SoftSpec)라 표시 전용 review보다 게이트가
*더* 중요 — 무검토 자동 적재가 랭킹을 조용히 바꾸면 안 된다.

설계 정합:
- **human commit gate(코드강제)**: 이 모듈은 DB writer(load_*_seed/write_facts)를 **import 안 함**.
  DB 접근은 select(읽기 전용)뿐. staging은 gitignore된 `data/staging/`라 자동 git 진입 불가.
- **규율 보존**: 속성별 파서를 그대로 호출 — gym `parse_output`(R1/R2 백스톱) · pet `parse_output`
  (confidence cap + 관리사무소 confirm flag) · review `parse_review_output`(저작권 캡 220). 게이트가
  규율을 우회/약화하지 않는다.
- **멱등·재개**: select가 DB-fresh(has_fresh) + 이미 staging된 단지(exclude_ids)를 제외. 중간에
  끊겨도 다음 run이 fresh/staging skip으로 이어간다(재질의·중복 추출 방지).
- **dedup**: 파서가 (complex_id, source_url) dedup, append_staging이 staging 누적분과 재dedup.

promote(사람 단계, 이 모듈 밖) — 속성별:
  1) `data/staging/<attr>.jsonl` spot-audit(값↔source 대조). gym/pet은 랭킹 영향이라 특히 엄격히.
  2) 검증된 줄을 `data/seeds/<attr>_*.jsonl`로 옮기고 git commit(사람 리뷰 후).
  3) `load_<attr>_seed`(gym/pet/review)로 DB 적재(라이브 write — 사람 트리거).
자세한 절차·cron 예시는 docs/auto-enrich.md.

  uv run python scripts/enrich_cron.py --attribute gym --limit 20
  uv run python scripts/enrich_cron.py --attribute pet --limit 10 --max-turns 80
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import _bootstrap  # noqa: F401  (side-effect: apps/api를 sys.path에 — PYTHONPATH 불필요)

# auto_enrich building block — **읽기/추출/파싱/규율만**(DB writer는 import 안 함 — 게이트).
# ATTR_CONFIG는 prompt/attribute/states/state_key/kind만 읽는다(["loader"]는 미사용).
from auto_enrich import (
    ATTR_CONFIG,
    ClaudeRunner,
    _default_runner,
    build_prompt,
    parse_output,
    parse_review_output,
    select_candidates,
)

from app.store.db import DEFAULT_DB_PATH, get_connection, init_db

# cron이 다루는 속성 — floorplan은 on-demand no-go(SPIKE)라 제외(gym/pet/review만).
CRON_ATTRS = ("gym", "pet", "review")
STAGING_DIR = Path(__file__).resolve().parents[1] / "data" / "staging"

# 속성별 파서 라우팅 — 각 규율을 그대로 보존(gym R1/R2 · pet cap+flag는 parse_output 내부).
Parser = Callable[[str, set[str]], list[dict[str, object]]]


def staging_path(attr: str) -> Path:
    """속성별 staging JSONL 경로 — gitignore된 data/staging/<attr>.jsonl."""
    return STAGING_DIR / f"{attr}.jsonl"


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


def parse_records(attr: str, text: str, valid_ids: set[str]) -> list[dict[str, object]]:
    """속성별 파서로 라우팅 — **규율 그대로 보존**(게이트가 약화하지 않음).

    review → parse_review_output(저작권 캡 220) · gym/pet → parse_output(gym R1/R2 백스톱,
    pet confidence cap + 관리사무소 confirm flag). 도메인 밖 상태는 파서가 unknown 강등.
    """
    cfg = ATTR_CONFIG[attr]
    if cfg.get("kind") == "review":
        return parse_review_output(text, valid_ids)
    return parse_output(
        text, str(cfg["attribute"]), valid_ids, set(cfg["states"]), str(cfg["state_key"])  # type: ignore[arg-type]
    )


def run_cron(
    conn: sqlite3.Connection,  # 읽기 전용 — select만(DB write 없음)
    attr: str,
    *,
    now: datetime,
    limit: int,
    max_turns: int,
    runner: ClaudeRunner = _default_runner,
    staging_path_override: Path | None = None,
) -> dict[str, int]:
    """속성 cron 1배치: select(fresh+staging 제외)→claude→parse→**staging append**. DB write 0.

    멱등·재개: DB-fresh + 이미 staging된 단지를 둘 다 제외해 재질의/중복 추출을 막는다.
    무후보면 즉시 0 반환(claude 미호출). {selected, extracted, staged} 반환.
    """
    if attr not in CRON_ATTRS:
        raise ValueError(f"지원하지 않는 속성: {attr} (가능: {', '.join(CRON_ATTRS)})")
    cfg = ATTR_CONFIG[attr]
    attribute = str(cfg["attribute"])
    sp = staging_path_override or staging_path(attr)

    excluded = staged_complex_ids(sp)
    candidates = select_candidates(conn, attribute, now=now, limit=limit, exclude_ids=excluded)
    if not candidates:
        return {"selected": 0, "extracted": 0, "staged": 0}

    prompt = build_prompt(str(cfg["prompt"]), candidates)
    output = runner(prompt, max_turns)
    valid_ids = {c["complex_id"] for c in candidates}
    records = parse_records(attr, output, valid_ids)
    staged = append_staging(sp, records)
    return {"selected": len(candidates), "extracted": len(records), "staged": staged}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="enrich_cron",
        description="enrichment cron — staging까지만(human commit gate). DB·git write 안 함.",
    )
    parser.add_argument("--attribute", choices=list(CRON_ATTRS), required=True)
    parser.add_argument("--limit", type=int, default=20, help="이번 run 단지 수(저volume 권장)")
    parser.add_argument("--max-turns", type=int, default=80, help="claude -p turn 상한")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite 경로(읽기전용)")
    parser.add_argument("--staging", default="", help="staging 경로 override(기본 <attr>.jsonl)")
    args = parser.parse_args(argv)

    conn = get_connection(args.db)
    init_db(conn)
    override = Path(args.staging) if args.staging else None
    stats = run_cron(
        conn,
        args.attribute,
        now=datetime.now(UTC),
        limit=args.limit,
        max_turns=args.max_turns,
        staging_path_override=override,
    )
    sp = override or staging_path(args.attribute)
    print(
        f"[enrich-cron:{args.attribute}] 선택 {stats['selected']} · 추출 {stats['extracted']} · "
        f"staging {stats['staged']} → {sp}"
    )
    print("※ staging까지만 — spot-audit 후 promote 절차는 docs/auto-enrich.md 참고.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
