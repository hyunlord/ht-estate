"""review_cron — staging-only(human commit gate)·fresh+staging skip 멱등·append dedup·랭킹 불변.

키리스(claude -p mock). 핵심 불변식: **자동 경로는 라이브 DB에 쓰지 않는다**(staging까지만).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.enrich.store import EnrichmentFact, has_fresh, write_facts
from app.search.spec import SoftSpec
from app.store.db import get_connection, init_db

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import review_cron  # noqa: E402
from auto_enrich import select_candidates  # noqa: E402
from review_cron import (  # noqa: E402
    REVIEW_ATTRIBUTE,
    append_staging,
    run_cron,
    staged_complex_ids,
)

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
TTL = timedelta(days=90)


def _db(*complexes: tuple[str, str, int]) -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.executemany(
        "INSERT INTO complex (complex_id, name, household_count) VALUES (?, ?, ?)", complexes
    )
    conn.commit()
    return conn


def _review_line(cid: str, url: str = "", summary: str = "살기 좋다는 평이 많다.") -> str:
    return json.dumps(
        {
            "complex_id": cid,
            "name": f"단지{cid}",
            "summary": summary,
            "points": ["교통 편리", "층간소음"],
            "confidence": 0.4,
            "source_type": "web",
            "source_url": url or f"https://zippoom.com/review/{cid}",
        },
        ensure_ascii=False,
    )


def _enrichment_count(conn: sqlite3.Connection, attribute: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM enrichment WHERE attribute = ?", (attribute,)
    ).fetchone()["n"]


# ───────────────────────── 핵심 게이트: staging-only(라이브 DB write 0) ─────────────────────────


def test_run_cron_writes_staging_only_not_db(tmp_path: Path) -> None:
    conn = _db(("A", "큰단지", 500), ("B", "중단지", 300))
    staging = tmp_path / "review.jsonl"

    def runner(prompt: str, max_turns: int) -> str:
        assert "큰단지" in prompt  # 후보가 프롬프트에
        return "\n".join(_review_line(c) for c in ("A", "B"))

    stats = run_cron(conn, now=NOW, limit=10, max_turns=80, runner=runner, staging_path=staging)
    assert stats == {"selected": 2, "extracted": 2, "staged": 2}
    # staging엔 기록됐지만…
    assert staging.exists() and len(staging.read_text().strip().splitlines()) == 2
    # **라이브 DB(enrichment)엔 한 줄도 안 썼다** — human commit gate(코드 강제).
    assert _enrichment_count(conn, REVIEW_ATTRIBUTE) == 0
    assert not has_fresh(conn, "A", REVIEW_ATTRIBUTE, now=NOW)


def test_module_does_not_expose_db_writers() -> None:
    # 코드 강제: cron 모듈은 DB writer를 import조차 하지 않는다(자동 write 구조적 차단).
    assert not hasattr(review_cron, "write_facts")
    assert not hasattr(review_cron, "load_seed")
    assert not hasattr(review_cron, "load_review_seed")


def test_default_staging_is_gitignored_location() -> None:
    # 기본 staging은 data/staging/ — gitignore된 위치(무검토 출력이 자동 git 진입 불가).
    assert review_cron.DEFAULT_STAGING.parent.name == "staging"
    assert review_cron.DEFAULT_STAGING.parent.parent.name == "data"


# ───────────────────────── 멱등·재개 (fresh + staging skip) ─────────────────────────


def test_run_cron_idempotent_skips_already_staged(tmp_path: Path) -> None:
    conn = _db(("A", "a", 500), ("B", "b", 300))
    staging = tmp_path / "review.jsonl"

    def runner(prompt: str, max_turns: int) -> str:
        return "\n".join(_review_line(c) for c in ("A", "B"))

    run_cron(conn, now=NOW, limit=10, max_turns=80, runner=runner, staging_path=staging)

    # 재실행: 둘 다 이미 staging → select 0 → claude 미호출(재질의 방지).
    def fail_runner(prompt: str, max_turns: int) -> str:
        raise AssertionError("이미 staging된 단지를 재질의하면 안 됨")

    stats2 = run_cron(
        conn, now=NOW, limit=10, max_turns=80, runner=fail_runner, staging_path=staging
    )
    assert stats2 == {"selected": 0, "extracted": 0, "staged": 0}


def test_run_cron_skips_db_fresh(tmp_path: Path) -> None:
    conn = _db(("A", "a", 500), ("B", "b", 300))
    # A는 이미 DB에 review fresh(promote 완료된 단지) → cron 재선택 금지.
    write_facts(
        conn, "A", REVIEW_ATTRIBUTE,
        [EnrichmentFact(value=json.dumps({"summary": "x", "points": []}),
                        confidence=0.4, source_type="web", source_url="https://seed/a")],
        ttl=TTL, now=NOW,
    )
    staging = tmp_path / "review.jsonl"

    def runner(prompt: str, max_turns: int) -> str:
        assert "단지A" not in prompt  # A는 fresh라 후보 아님
        return _review_line("B")

    stats = run_cron(conn, now=NOW, limit=10, max_turns=80, runner=runner, staging_path=staging)
    assert stats == {"selected": 1, "extracted": 1, "staged": 1}
    assert staged_complex_ids(staging) == {"B"}


def test_run_cron_no_candidates_no_claude_call(tmp_path: Path) -> None:
    conn = _db(("A", "a", 500))
    staging = tmp_path / "review.jsonl"
    append_staging(staging, [json.loads(_review_line("A"))])  # A 이미 staging

    def fail_runner(prompt: str, max_turns: int) -> str:
        raise AssertionError("무후보면 claude 미호출이어야")

    assert run_cron(
        conn, now=NOW, limit=10, max_turns=80, runner=fail_runner, staging_path=staging
    ) == {"selected": 0, "extracted": 0, "staged": 0}


# ───────────────────────── staging append dedup ─────────────────────────


def test_append_staging_dedups_same_pair(tmp_path: Path) -> None:
    staging = tmp_path / "review.jsonl"
    rec = json.loads(_review_line("A", url="https://zippoom.com/review/A"))
    assert append_staging(staging, [rec]) == 1
    assert append_staging(staging, [rec]) == 0  # 같은 (단지, 출처) 재append 안 됨
    assert len(staging.read_text().strip().splitlines()) == 1


def test_append_staging_allows_multi_source_same_complex(tmp_path: Path) -> None:
    staging = tmp_path / "review.jsonl"
    a1 = json.loads(_review_line("A", url="https://zippoom.com/review/A"))
    a2 = json.loads(_review_line("A", url="https://tenant.zaritalk.com/A"))  # 같은 단지·다른 출처
    assert append_staging(staging, [a1, a2]) == 2  # 다출처 허용
    assert staged_complex_ids(staging) == {"A"}


def test_append_staging_dedups_within_batch(tmp_path: Path) -> None:
    staging = tmp_path / "review.jsonl"
    rec = json.loads(_review_line("A", url="https://zippoom.com/review/A"))
    assert append_staging(staging, [rec, dict(rec)]) == 1  # 배치 내 중복도 1줄


# ───────────────────────── exclude_ids (select_candidates 확장) ─────────────────────────


def test_select_candidates_exclude_ids() -> None:
    conn = _db(("A", "a", 500), ("B", "b", 400), ("C", "c", 300))
    picked = select_candidates(
        conn, REVIEW_ATTRIBUTE, now=NOW, limit=10, exclude_ids={"A", "C"}
    )
    assert [c["complex_id"] for c in picked] == ["B"]  # 제외 외 나머지
    # 빈 exclude는 무영향(회귀 0)
    assert len(select_candidates(conn, REVIEW_ATTRIBUTE, now=NOW, limit=10, exclude_ids=set())) == 3


# ───────────────────────── 랭킹 불변 (review 표시 전용) ─────────────────────────


def test_review_is_display_only_not_in_ranking() -> None:
    # 구조적 보장: review는 조건 레지스트리 밖 → 랭킹 신호 불가(cron이 이를 바꾸지 않는다).
    from app.search.criteria import REGISTRY

    assert "review" not in REGISTRY and "review_summary" not in REGISTRY
    assert {"gym", "pet"} <= set(SoftSpec.model_fields)
