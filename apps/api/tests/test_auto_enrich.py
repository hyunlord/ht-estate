"""auto_enrich — 선택·규율강제 파싱·append·적재·멱등·캡 전달 (키리스, claude -p mock)."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.enrich.store import has_fresh, read_facts, write_facts
from app.store.db import get_connection, init_db

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from auto_enrich import (  # noqa: E402
    append_seed,
    auto_enrich,
    build_prompt,
    parse_output,
    select_candidates,
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


# ───────────────────────── 선택 ─────────────────────────


def test_select_skips_fresh_and_orders_by_household() -> None:
    conn = _db(("A", "큰단지", 500), ("B", "중단지", 300), ("C", "작은단지", 100))
    write_facts(conn, "B", "gym", [_fact_gym()], ttl=TTL, now=NOW)  # B는 fresh → 제외
    picked = select_candidates(conn, "gym", now=NOW, limit=10)
    assert [c["complex_id"] for c in picked] == ["A", "C"]  # 세대수 desc, B 제외


def test_select_respects_limit() -> None:
    conn = _db(("A", "a", 500), ("B", "b", 400), ("C", "c", 300))
    assert len(select_candidates(conn, "gym", now=NOW, limit=2)) == 2


# ───────────────────────── 규율 강제 파싱 ─────────────────────────

GYM_STATES = {"yes", "no", "unknown"}
PET_STATES = {"yes", "conditional", "no", "unknown"}


def test_parse_drops_blocked_domain_and_hallucinated_id() -> None:
    out = "\n".join([
        json.dumps({"complex_id": "A", "has_gym": "yes", "evidence": "공식 피트니스",
                    "confidence": 0.8, "source_type": "official", "source_url": "https://x/1"}),
        json.dumps({"complex_id": "A2", "has_gym": "yes", "evidence": "네이버",  # 차단도메인
                    "confidence": 0.7, "source_type": "web", "source_url": "https://m.naver.com/y"}),
        json.dumps({"complex_id": "GHOST", "has_gym": "yes", "evidence": "환각",  # valid_ids 밖
                    "confidence": 0.9, "source_type": "web", "source_url": "https://z/1"}),
    ])
    recs = parse_output(out, "gym", {"A", "A2"}, GYM_STATES, "has_gym")
    assert [r["complex_id"] for r in recs] == ["A"]  # 차단도메인·환각 drop


def test_parse_coerces_invalid_state_to_unknown() -> None:
    out = json.dumps({"complex_id": "A", "has_gym": "maybe", "evidence": "애매",
                      "confidence": 0.5, "source_type": "web", "source_url": "urn:auto:A"})
    recs = parse_output(out, "gym", {"A"}, GYM_STATES, "has_gym")
    assert recs[0]["has_gym"] == "unknown"  # 도메인 밖 → 보수적 unknown


def test_parse_drops_missing_source_or_evidence() -> None:
    out = "\n".join([
        json.dumps({"complex_id": "A", "has_gym": "yes", "evidence": "", "confidence": 0.8,
                    "source_type": "web", "source_url": "https://x/1"}),  # evidence 없음
        json.dumps({"complex_id": "B", "has_gym": "yes", "evidence": "ok", "confidence": 0.8,
                    "source_type": "web", "source_url": ""}),  # source 없음
    ])
    assert parse_output(out, "gym", {"A", "B"}, GYM_STATES, "has_gym") == []


def test_parse_pet_defaults_confirm_and_normalizes_caveats() -> None:
    out = "\n".join([
        json.dumps({"complex_id": "A", "pet_allowed": "conditional", "evidence": "제한 허용",
                    "caveats": ["견종 제한"], "confidence": 0.55, "source_type": "news",
                    "source_url": "https://mk/1"}),  # confirm 누락 → true 강제
        json.dumps({"complex_id": "B", "pet_allowed": "unknown", "evidence": "불명",
                    "confidence": 0.2, "source_type": "agent_research",
                    "source_url": "urn:ht-estate:auto:B"}),  # caveats 누락 → []
    ])
    recs = parse_output(out, "pet_allowed", {"A", "B"}, PET_STATES, "pet_allowed")
    assert all(r["confirm_with_office"] is True for r in recs)  # 전수 true
    assert recs[0]["caveats"] == ["견종 제한"]
    assert recs[1]["caveats"] == []


def test_parse_tolerates_codefence_and_prose() -> None:
    out = "다음은 결과입니다:\n```json\n" + json.dumps(
        {"complex_id": "A", "has_gym": "no", "evidence": "구축 시설없음", "confidence": 0.6,
         "source_type": "agent_research", "source_url": "urn:ht-estate:auto:A"}) + "\n```"
    recs = parse_output(out, "gym", {"A"}, GYM_STATES, "has_gym")
    assert len(recs) == 1 and recs[0]["has_gym"] == "no"


def test_parse_dedups_same_complex() -> None:
    line = json.dumps({"complex_id": "A", "has_gym": "yes", "evidence": "e", "confidence": 0.8,
                       "source_type": "web", "source_url": "https://x/1"})
    recs = parse_output(line + "\n" + line, "gym", {"A"}, GYM_STATES, "has_gym")
    assert len(recs) == 1


# ───────────────────────── append ─────────────────────────


def test_append_seed_accumulates(tmp_path: Path) -> None:
    p = tmp_path / "gym.jsonl"
    append_seed(p, [{"complex_id": "A", "has_gym": "yes"}])
    append_seed(p, [{"complex_id": "B", "has_gym": "no"}])
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2  # append 누적
    assert json.loads(lines[1])["complex_id"] == "B"


# ───────────────────────── 엔드투엔드 (mock runner) ─────────────────────────


def _fact_gym():  # type: ignore[no-untyped-def]
    from app.enrich.store import EnrichmentFact
    return EnrichmentFact(value=json.dumps({"has_gym": "yes", "evidence": "x"}),
                          confidence=0.8, source_type="official", source_url="https://seed/x")


def test_auto_enrich_end_to_end_and_idempotent(tmp_path: Path) -> None:
    conn = _db(("A", "큰단지", 500), ("B", "중단지", 300))
    captured: dict[str, int] = {}

    def mock_runner(prompt: str, max_turns: int) -> str:
        captured["max_turns"] = max_turns
        assert "큰단지" in prompt  # 후보가 프롬프트에 들어감
        return "\n".join(
            json.dumps({"complex_id": cid, "has_gym": "yes", "evidence": "단지 내 피트니스",
                        "confidence": 0.8, "source_type": "official",
                        "source_url": f"https://o/{cid}"})
            for cid in ("A", "B")
        )

    stats = auto_enrich(conn, "gym", now=NOW, ttl=TTL, limit=10, max_turns=42,
                        runner=mock_runner, seeds_dir=tmp_path)
    assert stats == {"selected": 2, "extracted": 2, "appended": 2, "loaded": 2}
    assert captured["max_turns"] == 42  # 캡 전달
    assert has_fresh(conn, "A", "gym", now=NOW)  # DB 적재됨
    assert (tmp_path / "gym_gangnam.jsonl").exists()  # 시드 append됨

    # 멱등: 재실행 시 미적재 단지 0 → claude 미호출.
    def fail_runner(prompt: str, max_turns: int) -> str:
        raise AssertionError("멱등이어야 하는데 claude 호출됨")

    stats2 = auto_enrich(conn, "gym", now=NOW, ttl=TTL, limit=10, max_turns=42,
                         runner=fail_runner, seeds_dir=tmp_path)
    assert stats2 == {"selected": 0, "extracted": 0, "appended": 0, "loaded": 0}


def test_auto_enrich_pet_loads_caveats(tmp_path: Path) -> None:
    conn = _db(("A", "단지", 500))

    def runner(prompt: str, max_turns: int) -> str:
        return json.dumps({"complex_id": "A", "pet_allowed": "conditional", "evidence": "제한 허용",
                           "caveats": ["견종 제한"], "confidence": 0.55, "source_type": "news",
                           "source_url": "https://mk/1"})

    stats = auto_enrich(conn, "pet", now=NOW, ttl=TTL, limit=10, max_turns=10,
                        runner=runner, seeds_dir=tmp_path)
    assert stats["loaded"] == 1
    facts = read_facts(conn, "A", "pet_allowed", now=NOW)
    parsed = json.loads(facts[0].value)
    assert parsed["pet_allowed"] == "conditional"
    assert parsed["caveats"] == ["견종 제한"]
    assert parsed["confirm_with_office"] is True


# ───────────────────────── R1/R2 백스톱 (적대적 스모크) ─────────────────────────


def test_r2_wiki_source_caps_confidence() -> None:
    # 위키 출처 conf 0.9 입력 → 0.5로 cap(drop 아님).
    out = json.dumps({"complex_id": "A", "has_gym": "yes", "evidence": "단지 내 피트니스센터 보유",
                      "confidence": 0.9, "source_type": "web",
                      "source_url": "https://namu.wiki/w/고덕그라시움"})
    recs = parse_output(out, "gym", {"A"}, GYM_STATES, "has_gym")
    assert recs[0]["has_gym"] == "yes"  # 토큰 있음 → yes 유지
    assert recs[0]["confidence"] == 0.5  # 위키 cap


def test_r2_gym_yes_requires_explicit_token() -> None:
    # generic '체육시설' yes → 명시 헬스장 토큰 없어 unknown 강등.
    out = json.dumps({"complex_id": "A", "has_gym": "yes", "evidence": "단지 내 체육시설 보유",
                      "confidence": 0.8, "source_type": "web", "source_url": "https://x/1"})
    recs = parse_output(out, "gym", {"A"}, GYM_STATES, "has_gym")
    assert recs[0]["has_gym"] == "unknown"  # 명시 토큰 없음 → 강등


def test_r2_gym_yes_with_token_survives() -> None:
    out = json.dumps({"complex_id": "A", "has_gym": "yes", "evidence": "입주민 전용 헬스장 운영",
                      "confidence": 0.8, "source_type": "official", "source_url": "https://x/1"})
    recs = parse_output(out, "gym", {"A"}, GYM_STATES, "has_gym")
    assert recs[0]["has_gym"] == "yes" and recs[0]["confidence"] == 0.8  # 토큰+비위키 → 유지


def test_r1_future_only_demotes_to_unknown() -> None:
    # 증설 '추진'(미래)만 + 완공 마커 없음 → yes 강등.
    out = json.dumps({"complex_id": "A", "has_gym": "yes",
                      "evidence": "커뮤니티 피트니스 증설 추진 예정", "confidence": 0.7,
                      "source_type": "news", "source_url": "https://x/1"})
    recs = parse_output(out, "gym", {"A"}, GYM_STATES, "has_gym")
    assert recs[0]["has_gym"] == "unknown"  # 미래만 → 강등


def test_r1_future_with_completion_survives() -> None:
    # 증설했고 '신설/운영' 완공 마커 있음 → yes 유지(false-pos 방지).
    out = json.dumps({"complex_id": "A", "has_gym": "yes",
                      "evidence": "증설 공사로 피트니스센터를 신설해 운영 중", "confidence": 0.7,
                      "source_type": "news", "source_url": "https://x/1"})
    recs = parse_output(out, "gym", {"A"}, GYM_STATES, "has_gym")
    assert recs[0]["has_gym"] == "yes"  # 완공 마커 있음 → 유지


def test_r1_pet_conditional_future_demotes() -> None:
    out = json.dumps({"complex_id": "A", "pet_allowed": "conditional", "evidence": "규약 개정 추진",
                      "caveats": [], "confidence": 0.5, "source_type": "news",
                      "source_url": "https://x/1"})
    recs = parse_output(out, "pet_allowed", {"A"}, PET_STATES, "pet_allowed")
    assert recs[0]["pet_allowed"] == "unknown"  # 미래만 → 강등


def test_default_runner_grants_readonly_web_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    # _default_runner가 claude -p에 읽기전용 웹도구(WebSearch/WebFetch)를 사전승인하는지.
    import auto_enrich

    captured: dict[str, list[str]] = {}

    class _Proc:
        stdout = "{}"

    def fake_run(argv, **kw):  # type: ignore[no-untyped-def]
        captured["argv"] = argv
        return _Proc()

    monkeypatch.setattr(auto_enrich.subprocess, "run", fake_run)
    auto_enrich._default_runner("prompt", 30)
    argv = captured["argv"]
    assert argv[:2] == ["claude", "-p"]
    assert "--allowedTools" in argv
    assert "WebSearch" in argv and "WebFetch" in argv  # 읽기전용 웹도구
    assert "--max-turns" in argv and "30" in argv
    # 파일쓰기 도구는 미승인(안전 — 시드 append는 부모만)
    assert "Write" not in argv and "Edit" not in argv


def test_build_prompt_injects_candidates() -> None:
    p = build_prompt("enrich_gym.md", [{"complex_id": "A", "name": "단지A"}])
    assert "단지A" in p
    assert "{CANDIDATES_JSON}" not in p  # 치환됨
    assert "차단" in p or "naver" in p  # 규율 텍스트 존재
