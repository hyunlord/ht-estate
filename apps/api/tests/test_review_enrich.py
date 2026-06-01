"""review 파서 규율 (P3-1) — 환각/출처/차단/근거 drop · 저작권 길이 캡 · 다출처 (키리스)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from auto_enrich import (  # noqa: E402
    REVIEW_MAX_POINTS,
    REVIEW_SUMMARY_CAP,
    parse_review_output,
)

VALID = {"A", "B"}


def _line(**kw: object) -> str:
    base = {
        "complex_id": "A", "name": "단지", "summary": "조용하고 살기 좋다는 평.",
        "points": ["조용함"], "confidence": 0.4, "source_type": "youtube",
        "source_url": "https://youtube.com/watch?v=x",
    }
    base.update(kw)
    return json.dumps(base, ensure_ascii=False)


def test_drops_hallucinated_complex() -> None:
    out = parse_review_output(_line(complex_id="ZZZ"), VALID)
    assert out == []  # 후보 밖 단지 drop


def test_requires_source_url() -> None:
    assert parse_review_output(_line(source_url=""), VALID) == []
    # http도 urn도 아니면 drop
    assert parse_review_output(_line(source_url="ftp://x"), VALID) == []


def test_drops_blocked_domain() -> None:
    assert parse_review_output(_line(source_url="https://blog.naver.com/x"), VALID) == []
    assert parse_review_output(_line(source_url="https://hogangnono.com/x"), VALID) == []


def test_drops_empty_summary() -> None:
    assert parse_review_output(_line(summary="   "), VALID) == []  # 근거 없음


def test_caps_summary_length_copyright_backstop() -> None:
    long_summary = "가" * 500  # 원문 재현 시도
    out = parse_review_output(_line(summary=long_summary), VALID)
    assert len(out) == 1
    stored = out[0]["summary"]
    assert isinstance(stored, str)
    assert len(stored) <= REVIEW_SUMMARY_CAP + 1  # 캡 + 말줄임표
    assert stored.endswith("…")  # 절단 표시


def test_caps_points_count_and_length() -> None:
    out = parse_review_output(
        _line(points=["p" * 200] + [f"포인트{i}" for i in range(10)]), VALID
    )
    pts = out[0]["points"]
    assert isinstance(pts, list)
    assert len(pts) <= REVIEW_MAX_POINTS
    assert all(len(p) <= 61 for p in pts)  # 각 포인트 길이 캡(+말줄임)


def test_confidence_clamped() -> None:
    assert parse_review_output(_line(confidence=9.0), VALID)[0]["confidence"] == 1.0
    assert parse_review_output(_line(confidence="bad"), VALID)[0]["confidence"] == 0.3


def test_keeps_multiple_sources_per_complex() -> None:
    # 같은 단지라도 출처가 다르면 여러 줄(다출처 §4). 같은 url 중복은 dedup.
    text = "\n".join([
        _line(source_url="https://youtube.com/a"),
        _line(source_url="https://tistory.com/b"),
        _line(source_url="https://youtube.com/a"),  # 중복 → dedup
    ])
    out = parse_review_output(text, VALID)
    assert len(out) == 2
    assert {r["source_url"] for r in out} == {"https://youtube.com/a", "https://tistory.com/b"}


def test_wiki_confidence_capped() -> None:
    out = parse_review_output(
        _line(confidence=0.9, source_url="https://namu.wiki/w/x"), VALID
    )
    conf = out[0]["confidence"]
    assert isinstance(conf, float) and conf <= 0.5  # R2 위키 cap
