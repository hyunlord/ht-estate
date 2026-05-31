"""enrichment 자동 prefill — cron'd `claude -p`(headless, 키리스)로 미적재 단지 gym/pet 추출.

C4/C5/C7의 (a) 수동 시드를 무인 자동화: ① fresh 없는 단지 N개 선택 → ② `claude -p`에
규율 프롬프트+단지목록 전달 → ③ 출력(JSONL) 파싱·**규율 강제**(차단도메인 drop·상태 도메인
강제·pet confirm 기본 true) → ④ 시드 JSONL append → ⑤ 로더 적재. 멱등·재개(has_fresh skip).

**배치 prefill**(라이브 lazy 아님): 미리 채운 단지만 검색 시 즉답. 저volume(구독 rate 보호) ·
시간 두고 누적. 무검토라 (i) 프롬프트 보수적 + (ii) 파서가 규율 강제 + (iii) 출처 전수 기록 →
주기적 사람 spot-audit 권장. 시드 자동 git commit 안 함(사람 리뷰 후 commit).

    uv run python scripts/auto_enrich.py --attribute gym --limit 20
    uv run python scripts/auto_enrich.py --attribute both --limit 30 --max-turns 60

cron 예시·안전장치는 docs/auto-enrich.md.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import _bootstrap  # noqa: F401  (side-effect: apps/api를 sys.path에 — PYTHONPATH 불필요)
import load_gym_seed
import load_pet_seed

from app.store.db import DEFAULT_DB_PATH, get_connection, init_db

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
SEEDS_DIR = Path(__file__).resolve().parents[1] / "data" / "seeds"
BLOCKED_DOMAINS = ("naver.com", "hogangnono", "hogengnono", "asil.kr")
DEFAULT_TTL = timedelta(days=90)

# 속성별 설정 — 프롬프트·시드파일·로더·상태 도메인. 새 속성은 여기 한 줄.
ATTR_CONFIG: dict[str, dict[str, object]] = {
    "gym": {
        "attribute": "gym",
        "prompt": "enrich_gym.md",
        "seed": "gym_gangnam.jsonl",
        "loader": load_gym_seed,
        "state_key": "has_gym",
        "states": {"yes", "no", "unknown"},
    },
    "pet": {
        "attribute": "pet_allowed",
        "prompt": "enrich_pet.md",
        "seed": "pet_gangnam.jsonl",
        "loader": load_pet_seed,
        "state_key": "pet_allowed",
        "states": {"yes", "conditional", "no", "unknown"},
    },
}

# 주입형 claude 러너 — (prompt, max_turns) → stdout. 테스트는 mock으로 키리스.
ClaudeRunner = Callable[[str, int], str]

# 읽기 전용 웹 도구만 사전승인 — 추출기가 공개 출처를 검색·인용(http source_url)하게 한다.
# 미승인 시 headless claude는 웹을 못 봐 urn/agent_research로만 떨어지거나 출력이 비는 것을
# 라이브 검증(C13)에서 확인. 파일 쓰기 등은 미승인(시드 append는 부모 드라이버만) → 안전.
CLAUDE_WEB_TOOLS = ["WebSearch", "WebFetch"]


def _default_runner(prompt: str, max_turns: int) -> str:
    """`claude -p`(headless, 구독 인증) 호출 + 읽기 전용 웹 도구. stdout 반환. API 키 불필요."""
    proc = subprocess.run(
        ["claude", "-p", prompt, "--allowedTools", *CLAUDE_WEB_TOOLS,
         "--max-turns", str(max_turns)],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout


def select_candidates(
    conn: sqlite3.Connection, attribute: str, *, now: datetime, limit: int
) -> list[dict[str, str]]:
    """fresh enrichment 없는 단지 limit개 — 세대수 desc 우선(영향 큰 단지 먼저)."""
    rows = conn.execute(
        "SELECT c.complex_id, c.name FROM complex c "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM enrichment e WHERE e.complex_id = c.complex_id "
        "  AND e.attribute = ? AND e.ttl_expires_at > ?) "
        "ORDER BY c.household_count DESC NULLS LAST, c.complex_id "
        "LIMIT ?",
        (attribute, now.isoformat(), limit),
    ).fetchall()
    return [{"complex_id": r["complex_id"], "name": r["name"] or ""} for r in rows]


def build_prompt(prompt_name: str, candidates: list[dict[str, str]]) -> str:
    """프롬프트 템플릿 + 후보 JSON 치환."""
    template = (PROMPTS_DIR / prompt_name).read_text(encoding="utf-8")
    return template.replace("{CANDIDATES_JSON}", json.dumps(candidates, ensure_ascii=False))


def _iter_json_objects(text: str):  # type: ignore[no-untyped-def]
    """모델 출력에서 JSON 객체를 관용적으로 추출(코드펜스·잡설 무시, 줄당 1객체)."""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("```") or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield obj


def parse_output(
    text: str, attribute: str, valid_ids: set[str], states: set[str], state_key: str
) -> list[dict[str, object]]:
    """모델 출력 → 검증된 시드 레코드. **규율 강제**(무검토 안전):

    - complex_id가 요청 후보(valid_ids)에 있어야(환각 단지 drop).
    - source_url 필수 · 차단도메인이면 drop.
    - 상태가 도메인 밖/누락이면 'unknown'으로 강등(보수적).
    - source_url이 http도 urn도 아니면 drop(출처 추적 불가).
    - pet: confirm_with_office 누락 시 true 강제, caveats는 list로 정규화.
    """
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    for obj in _iter_json_objects(text):
        cid = str(obj.get("complex_id", ""))
        if cid not in valid_ids or cid in seen:
            continue
        url = str(obj.get("source_url", "")).strip()
        if not url or any(b in url for b in BLOCKED_DOMAINS):
            continue
        if not (url.startswith("http") or url.startswith("urn:")):
            continue
        evidence = str(obj.get("evidence", "")).strip()
        if not evidence:
            continue
        state = obj.get(state_key)
        if state not in states:
            state = "unknown"
        try:
            conf = float(obj.get("confidence", 0.2))
        except (TypeError, ValueError):
            conf = 0.2
        rec: dict[str, object] = {
            "complex_id": cid,
            "name": str(obj.get("name", "")),
            state_key: state,
            "evidence": evidence,
            "confidence": max(0.0, min(1.0, conf)),
            "source_type": str(obj.get("source_type", "agent_research")),
            "source_url": url,
        }
        if attribute == "pet_allowed":
            raw_caveats = obj.get("caveats")
            rec["caveats"] = [str(c) for c in raw_caveats] if isinstance(raw_caveats, list) else []
            rec["confirm_with_office"] = bool(obj.get("confirm_with_office", True))
        else:
            rec["in_complex"] = bool(obj.get("in_complex", state == "yes"))
        records.append(rec)
        seen.add(cid)
    return records


def append_seed(path: Path, records: list[dict[str, object]]) -> int:
    """검증된 레코드를 시드 JSONL에 append(누적). 쓴 줄 수 반환."""
    if not records:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(records)


def auto_enrich(
    conn: sqlite3.Connection,
    attr: str,
    *,
    now: datetime,
    ttl: timedelta = DEFAULT_TTL,
    limit: int,
    max_turns: int,
    runner: ClaudeRunner = _default_runner,
    seeds_dir: Path = SEEDS_DIR,
) -> dict[str, int]:
    """한 속성 자동 prefill: 선택→claude→파싱→append→적재. 멱등(미적재만 선택). 통계 반환."""
    cfg = ATTR_CONFIG[attr]
    attribute = str(cfg["attribute"])
    candidates = select_candidates(conn, attribute, now=now, limit=limit)
    if not candidates:
        return {"selected": 0, "extracted": 0, "appended": 0, "loaded": 0}

    prompt = build_prompt(str(cfg["prompt"]), candidates)
    output = runner(prompt, max_turns)
    valid_ids = {c["complex_id"] for c in candidates}
    records = parse_output(
        output, attribute, valid_ids, set(cfg["states"]), str(cfg["state_key"])  # type: ignore[arg-type]
    )

    seed_path = seeds_dir / str(cfg["seed"])
    appended = append_seed(seed_path, records)
    loader = cfg["loader"]
    stats = loader.load_seed(conn, records, ttl=ttl, now=now) if records else {"loaded": 0}  # type: ignore[attr-defined]
    return {
        "selected": len(candidates),
        "extracted": len(records),
        "appended": appended,
        "loaded": int(stats["loaded"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="auto_enrich", description="enrichment 자동 prefill")
    parser.add_argument("--attribute", choices=["gym", "pet", "both"], default="both")
    parser.add_argument("--limit", type=int, default=20, help="이번 run 단지 수(저volume 권장)")
    parser.add_argument("--max-turns", type=int, default=60, help="claude -p turn 상한")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite 경로")
    args = parser.parse_args(argv)

    conn = get_connection(args.db)
    init_db(conn)
    now = datetime.now(UTC)
    attrs = ["gym", "pet"] if args.attribute == "both" else [args.attribute]
    for attr in attrs:
        stats = auto_enrich(conn, attr, now=now, limit=args.limit, max_turns=args.max_turns)
        print(
            f"[{attr}] 선택 {stats['selected']} · 추출 {stats['extracted']} · "
            f"append {stats['appended']} · 적재 {stats['loaded']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
