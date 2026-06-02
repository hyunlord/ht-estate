"""enrich_cron — gym/pet/review 일반화: staging-only(DB write 0)·규율 보존·멱등·dedup·랭킹 불변.

키리스(claude -p mock). 핵심 불변식: **자동 경로는 라이브 DB에 쓰지 않는다**(staging까지만).
gym/pet은 랭킹 신호라 더 중요. auto_enrich CLI도 staging-only 강등됐는지(자동 적재 경로 0) 가드.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.enrich.store import EnrichmentFact, has_fresh, write_facts
from app.search.spec import SoftSpec
from app.store.db import get_connection, init_db

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import auto_enrich  # noqa: E402
import enrich_cron  # noqa: E402
from enrich_cron import (  # noqa: E402
    CRON_ATTRS,
    append_staging,
    parse_records,
    run_cron,
    staged_complex_ids,
    staging_path,
)

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
TTL = timedelta(days=90)

# 속성별 ATTR_CONFIG attribute(enrichment 테이블 키) — select/has_fresh/count에 쓴다.
ATTR_KEY = {"gym": "gym", "pet": "pet_allowed", "review": "review_summary"}


def _db(*complexes: tuple[str, str, int]) -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.executemany(
        "INSERT INTO complex (complex_id, name, household_count) VALUES (?, ?, ?)", complexes
    )
    conn.commit()
    return conn


def _enrichment_count(conn: sqlite3.Connection, attribute: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM enrichment WHERE attribute = ?", (attribute,)
    ).fetchone()["n"]


def _line(attr: str, cid: str, url: str = "") -> str:
    """속성별 유효 출력 한 줄(파서 통과용)."""
    src = url or f"https://example.com/{attr}/{cid}"
    if attr == "gym":
        return json.dumps({"complex_id": cid, "has_gym": "yes",
                           "evidence": "입주민 전용 헬스장 운영", "confidence": 0.8,
                           "source_type": "official", "source_url": src})
    if attr == "pet":
        return json.dumps({"complex_id": cid, "pet_allowed": "conditional",
                           "evidence": "견종 제한 허용", "caveats": ["견종 제한"],
                           "confidence": 0.55, "source_type": "news", "source_url": src})
    return json.dumps({"complex_id": cid, "name": f"단지{cid}", "summary": "살기 좋다는 평이 많다.",
                       "points": ["교통 편리"], "confidence": 0.4,
                       "source_type": "web", "source_url": src}, ensure_ascii=False)


# ───────────────────────── 핵심 게이트: staging-only(라이브 DB write 0) ─────────────────────────


@pytest.mark.parametrize("attr", ["gym", "pet", "review"])
def test_run_cron_staging_only_not_db(attr: str, tmp_path: Path) -> None:
    conn = _db(("A", "큰단지", 500), ("B", "중단지", 300))
    staging = tmp_path / f"{attr}.jsonl"

    def runner(prompt: str, max_turns: int) -> str:
        assert "큰단지" in prompt  # 후보가 프롬프트에
        return "\n".join(_line(attr, c) for c in ("A", "B"))

    stats = run_cron(conn, attr, now=NOW, limit=10, max_turns=80,
                     runner=runner, staging_path_override=staging)
    assert stats == {"selected": 2, "extracted": 2, "staged": 2}
    assert staging.exists() and len(staging.read_text().strip().splitlines()) == 2
    # **라이브 DB(enrichment)엔 한 줄도 안 썼다** — human commit gate.
    assert _enrichment_count(conn, ATTR_KEY[attr]) == 0
    assert not has_fresh(conn, "A", ATTR_KEY[attr], now=NOW)


def test_enrich_cron_module_exposes_no_db_writers() -> None:
    # 코드강제 게이트: cron 모듈은 DB writer를 import조차 안 한다.
    for name in ("write_facts", "load_seed", "load_gym_seed", "load_pet_seed", "load_review_seed"):
        assert not hasattr(enrich_cron, name), name


@pytest.mark.parametrize("attr", ["gym", "pet", "review"])
def test_staging_path_is_gitignored_location(attr: str) -> None:
    p = staging_path(attr)
    assert p.name == f"{attr}.jsonl"
    assert p.parent.name == "staging" and p.parent.parent.name == "data"


# ───────────────────────── 규율 보존 (속성별 파서 라우팅) ─────────────────────────


def test_gym_r2_discipline_preserved() -> None:
    # R2: 위키 출처 conf cap(0.5) + 명시 헬스장 토큰 없으면 yes→unknown — parse_records(gym) 경유.
    wiki = json.dumps({"complex_id": "A", "has_gym": "yes", "evidence": "단지 내 피트니스센터 보유",
                       "confidence": 0.9, "source_type": "web",
                       "source_url": "https://namu.wiki/w/x"})
    generic = json.dumps({"complex_id": "B", "has_gym": "yes", "evidence": "단지 내 체육시설 보유",
                          "confidence": 0.8, "source_type": "web", "source_url": "https://x/1"})
    recs = {r["complex_id"]: r for r in parse_records("gym", wiki + "\n" + generic, {"A", "B"})}
    assert recs["A"]["has_gym"] == "yes" and recs["A"]["confidence"] == 0.5  # 위키 cap
    assert recs["B"]["has_gym"] == "unknown"  # 명시 토큰 없음 → 강등


def test_pet_confirm_flag_preserved() -> None:
    # pet: confirm_with_office 누락 → true 강제(parse_records(pet) 경유).
    out = json.dumps({"complex_id": "A", "pet_allowed": "conditional", "evidence": "제한 허용",
                      "caveats": ["견종 제한"], "confidence": 0.55, "source_type": "news",
                      "source_url": "https://mk/1"})
    rec = parse_records("pet", out, {"A"})[0]
    assert rec["confirm_with_office"] is True and rec["caveats"] == ["견종 제한"]


def test_review_copyright_cap_preserved() -> None:
    # review: summary 220자 캡 절단(parse_records(review) 경유).
    long_summary = "가" * 400
    out = json.dumps({"complex_id": "A", "summary": long_summary, "points": [],
                      "confidence": 0.4, "source_type": "web", "source_url": "https://zippoom.com/r/A"},
                     ensure_ascii=False)
    rec = parse_records("review", out, {"A"})[0]
    assert len(str(rec["summary"])) <= 221  # 220 + '…'


# ───────────────────────── 멱등·재개·dedup ─────────────────────────


def test_idempotent_skips_staged_and_db_fresh(tmp_path: Path) -> None:
    conn = _db(("A", "a", 500), ("B", "b", 300), ("C", "c", 200))
    write_facts(conn, "A", "gym",  # A는 이미 DB fresh(promote 완료)
                [EnrichmentFact(value=json.dumps({"has_gym": "yes", "evidence": "x"}),
                                confidence=0.8, source_type="official", source_url="https://s/a")],
                ttl=TTL, now=NOW)
    staging = tmp_path / "gym.jsonl"

    def runner(prompt: str, max_turns: int) -> str:
        assert "단지A" not in prompt  # DB-fresh A 제외
        return _line("gym", "B")  # B만(C는 limit로)

    run_cron(conn, "gym", now=NOW, limit=1, max_turns=80,
             runner=runner, staging_path_override=staging)
    assert staged_complex_ids(staging) == {"B"}

    def fail_runner(prompt: str, max_turns: int) -> str:
        assert "단지B" not in prompt  # 이미 staging된 B도 제외(재개)
        return _line("gym", "C")

    run_cron(conn, "gym", now=NOW, limit=1, max_turns=80,
             runner=fail_runner, staging_path_override=staging)
    assert staged_complex_ids(staging) == {"B", "C"}  # B 재질의 안 함, C 추가


def test_append_dedup_and_multisource(tmp_path: Path) -> None:
    staging = tmp_path / "gym.jsonl"
    a1 = json.loads(_line("gym", "A", url="https://o/a1"))
    a2 = json.loads(_line("gym", "A", url="https://o/a2"))
    assert append_staging(staging, [a1, a2]) == 2  # 다출처 허용
    assert append_staging(staging, [a1]) == 0  # 같은 (단지,출처) 재append 안 됨


def test_invalid_attr_rejected(tmp_path: Path) -> None:
    conn = _db(("A", "a", 1))
    with pytest.raises(ValueError, match="지원하지 않는 속성"):
        run_cron(conn, "floorplan", now=NOW, limit=1, max_turns=10,
                 runner=lambda p, t: "", staging_path_override=tmp_path / "x.jsonl")
    assert "floorplan" not in CRON_ATTRS


# ───────────────────────── auto_enrich CLI 강등 (무검토 자동 적재 경로 0) ─────────────────────────


def test_auto_enrich_main_is_staging_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 강등 검증: auto_enrich.py main()이 라이브 DB에 안 쓰고 staging으로만 라우팅하는지.
    dbpath = tmp_path / "t.db"
    conn = get_connection(str(dbpath))
    init_db(conn)
    conn.execute("INSERT INTO complex (complex_id, name, household_count) VALUES ('A','단지A',500)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(enrich_cron, "STAGING_DIR", tmp_path)  # staging을 tmp로

    class _Proc:
        stdout = _line("gym", "A")

    monkeypatch.setattr(auto_enrich.subprocess, "run", lambda *a, **k: _Proc())  # claude -p mock

    rc = auto_enrich.main(["--attribute", "gym", "--limit", "5", "--db", str(dbpath)])
    assert rc == 0
    check = get_connection(str(dbpath))
    init_db(check)
    assert _enrichment_count(check, "gym") == 0  # **CLI가 DB에 안 씀**(강등됨)
    assert (tmp_path / "gym.jsonl").exists()  # staging엔 씀


# ───────────────────────── 랭킹 불변 ─────────────────────────


def test_ranking_invariant_softspec() -> None:
    # gym/pet은 랭킹 신호지만 무검토 자동 적재가 0이므로 랭킹은 사람 promote 전까지 불변.
    # review는 조건 레지스트리 밖 → 랭킹 신호 불가(P4-2a 일반화 후에도). gym/pet 후방호환 유지.
    from app.search.criteria import REGISTRY

    assert "review" not in REGISTRY and "review_summary" not in REGISTRY
    assert {"gym", "pet"} <= set(SoftSpec.model_fields)
