"""floorplan 파서 규율 (P3-2) — feature-only·null-tolerant·환각/점수화 거부·다출처 (키리스)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from auto_enrich import parse_floorplan_output  # noqa: E402

VALID = {"A", "B"}


def _line(**kw: object) -> str:
    base = {
        "complex_id": "A", "name": "단지", "bay": 3, "orientation": "남향",
        "structure": "판상형", "evidence": "전면 3실", "confidence": 0.5,
        "source_type": "agent_research", "source_url": "https://x/1",
    }
    base.update(kw)
    return json.dumps(base, ensure_ascii=False)


def test_drops_hallucinated_complex() -> None:
    assert parse_floorplan_output(_line(complex_id="ZZZ"), VALID) == []


def test_requires_source_url() -> None:
    assert parse_floorplan_output(_line(source_url=""), VALID) == []
    assert parse_floorplan_output(_line(source_url="ftp://x"), VALID) == []


def test_extracts_objective_features() -> None:
    out = parse_floorplan_output(_line(), VALID)
    assert len(out) == 1
    r = out[0]
    assert r["bay"] == 3 and r["orientation"] == "남향" and r["structure"] == "판상형"


def test_null_tolerant_partial_features() -> None:
    # 일부만 읽음(bay만) — null 허용, 보관.
    out = parse_floorplan_output(_line(orientation="모름", structure=None), VALID)
    assert len(out) == 1
    assert out[0]["bay"] == 3 and out[0]["orientation"] is None and out[0]["structure"] is None


def test_all_null_dropped_out_of_domain() -> None:
    # 세 feature 전부 불명(out-of-domain 단독주택 등) → drop(안전 degrade).
    assert parse_floorplan_output(_line(bay=None, orientation=None, structure=None), VALID) == []
    # 도메인 밖 값도 null 처리 → 전부 null → drop
    junk = _line(bay="많음", orientation="좋음", structure="멋짐")
    assert parse_floorplan_output(junk, VALID) == []


def test_rejects_scoring_and_out_of_domain_values() -> None:
    # 점수화/주관 토큰은 도메인 밖이라 null. structure는 판상/타워/혼합만.
    out = parse_floorplan_output(_line(structure="좋은구조", orientation="남향"), VALID)
    assert out[0]["structure"] is None  # '좋은구조'는 도메인 밖 → null(점수화 차단)
    assert out[0]["orientation"] == "남향"
    # bay 범위 밖/비정수 → null
    assert parse_floorplan_output(_line(bay=99), VALID)[0]["bay"] is None
    two = _line(bay=2.0, structure=None, orientation=None)
    assert parse_floorplan_output(two, VALID)[0]["bay"] == 2  # float 정수는 int로


def test_keeps_multiple_sources_per_complex() -> None:
    text = "\n".join([
        _line(source_url="https://a/1"),
        _line(source_url="https://b/2", bay=2),
        _line(source_url="https://a/1"),  # dup → drop
    ])
    out = parse_floorplan_output(text, VALID)
    assert len(out) == 2
    assert {r["source_url"] for r in out} == {"https://a/1", "https://b/2"}


def test_blocked_domain_dropped() -> None:
    assert parse_floorplan_output(_line(source_url="https://hogangnono.com/x"), VALID) == []
