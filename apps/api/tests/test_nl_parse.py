"""NL→spec 파서 (P4-2b) — 키리스 단위 테스트(claude -p 러너 mock).

레지스트리 grounding(미등록 거부·환각 drop·unsupported 표면화) · hard/soft 분류 · 모호→soft ·
감지 역산 · JSON 추출 관용성 · 모순 범위 거부 · 랭킹 연결(demote-not-exclude). 실 LLM 호출 0.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from app.search.criteria import REGISTRY
from app.search.nl_parse import (
    ParsedQuery,
    QueryParseError,
    _default_runner,
    build_parse_prompt,
    parse_query,
    registry_catalog,
)


def _runner(payload: object) -> Callable[[str, int], str]:
    """payload(dict 등)를 JSON 문자열로 돌려주는 mock 러너 — claude -p 대체."""

    def run(prompt: str, max_turns: int) -> str:
        return json.dumps(payload, ensure_ascii=False)

    return run


def _raw_runner(text: str) -> Callable[[str, int], str]:
    """원시 텍스트를 그대로 돌려주는 mock 러너(코드펜스·잡설·빈응답 테스트용)."""

    def run(prompt: str, max_turns: int) -> str:
        return text

    return run


def _parse(payload: object) -> ParsedQuery:
    return parse_query("질의", runner=_runner(payload))


# ───────────────────────── 레지스트리 매핑 ─────────────────────────


def test_maps_registry_conditions() -> None:
    """역세권→subway_time · 어린이집→has_daycare(hard) · 큰→household_count · 신축→approval_year ·
    강아지→pet(soft) — 의뢰서 핵심 매핑."""
    parsed = _parse(
        {
            "hard": {"has_daycare": True},
            "soft": {
                "gym": "none",
                "pet": "preferred",
                "criteria": [
                    {"key": "subway_time", "weight": 1.0},
                    {"key": "approval_year", "weight": 1.0},
                    {"key": "household_count", "weight": 1.0},
                ],
            },
            "detected": [],
            "unsupported": [],
        }
    )
    assert parsed.spec.has_daycare is True
    soft_keys = {c.key for c in parsed.spec.soft.criteria}
    assert soft_keys == {"subway_time", "approval_year", "household_count"}
    assert parsed.spec.soft.pet == "preferred"


def test_hard_specific_value_classification() -> None:
    """필수/특정값은 hard로 반영 — '전용 84 이상'→net_area_min, '신축 단지만'→approval_year_min."""
    parsed = _parse(
        {"hard": {"net_area_min": 84.0, "approval_year_min": 2018}, "soft": {}}
    )
    assert parsed.spec.net_area_min == 84.0
    assert parsed.spec.approval_year_min == 2018


def test_ambiguous_goes_soft_not_hard() -> None:
    """모호/비교형은 soft로 — 후보 SET을 떨구지 않게(demote-not-exclude). hard는 비어야."""
    parsed = _parse(
        {"hard": {}, "soft": {"criteria": [{"key": "approval_year", "weight": 1.0}]}}
    )
    assert parsed.spec.approval_year_min is None
    assert parsed.spec.approval_year_max is None
    assert {c.key for c in parsed.spec.soft.criteria} == {"approval_year"}


# ───────────────────────── grounding (환각 거부) ─────────────────────────


def test_rejects_hallucinated_soft_key() -> None:
    """미등록 soft key는 drop(거부) — 유효 key는 유지. 예외 안 던짐(보수)."""
    parsed = _parse(
        {
            "soft": {
                "criteria": [
                    {"key": "ocean_view", "weight": 1.0},  # 환각 — 레지스트리 밖
                    {"key": "subway_time", "weight": 1.0},
                ]
            }
        }
    )
    assert {c.key for c in parsed.spec.soft.criteria} == {"subway_time"}


def test_rejects_hardonly_key_in_soft() -> None:
    """hard-only 조건(heat_type·builder)은 soft criteria에서 거부(soft_able=False)."""
    parsed = _parse({"soft": {"criteria": [{"key": "heat_type", "weight": 1.0}]}})
    assert parsed.spec.soft.criteria == []


def test_ignores_hallucinated_hard_field() -> None:
    """미등록 hard 필드명은 drop — 유효 필드는 반영."""
    parsed = _parse({"hard": {"foo_bar": 1, "has_daycare": True}})
    assert parsed.spec.has_daycare is True
    assert "foo_bar" not in parsed.spec.model_dump()


def test_invalid_preference_coerced_to_none() -> None:
    """범위 밖 gym/pet preference는 none으로 강등(보수)."""
    parsed = _parse({"soft": {"gym": "MAYBE", "pet": "preferred"}})
    assert parsed.spec.soft.gym == "none"
    assert parsed.spec.soft.pet == "preferred"


def test_negative_weight_clamped() -> None:
    """음수 weight는 0으로 클램프(weight=0 → 끄기, demote-not-exclude 유지)."""
    parsed = _parse({"soft": {"criteria": [{"key": "subway_time", "weight": -5}]}})
    assert parsed.spec.soft.criteria[0].weight == 0.0


# ───────────────────────── unsupported 표면화 ─────────────────────────


def test_unsupported_surfaced() -> None:
    """매핑 불가 구절은 unsupported로 표면화(환각으로 spec에 안 들어감)."""
    parsed = _parse({"hard": {}, "soft": {}, "unsupported": ["바다 전망", "조용한"]})
    assert parsed.unsupported == ["바다 전망", "조용한"]


def test_unsupported_non_list_ignored() -> None:
    parsed = _parse({"unsupported": "바다 전망"})  # 리스트 아님 → 무시
    assert parsed.unsupported == []


# ───────────────────────── 감지 역산 ─────────────────────────


def test_detected_derived_from_spec_when_llm_omits() -> None:
    """LLM이 detected를 빠뜨려도 확정 spec에서 역산 — spec과 항상 정합."""
    parsed = _parse(
        {"hard": {"has_daycare": True}, "soft": {"criteria": [{"key": "subway_time"}]}}
    )
    by_key = {(d.criterion_key, d.mode) for d in parsed.detected}
    assert ("has_daycare", "hard") in by_key
    assert ("subway_time", "soft") in by_key


def test_detected_phrase_enriched_from_llm() -> None:
    """LLM이 준 phrase가 감지에 주석으로 실린다."""
    parsed = _parse(
        {
            "hard": {"has_daycare": True},
            "detected": [
                {"phrase": "어린이집 있는", "criterion_key": "has_daycare", "mode": "hard"}
            ],
        }
    )
    daycare = next(d for d in parsed.detected if d.criterion_key == "has_daycare")
    assert daycare.phrase == "어린이집 있는"
    assert daycare.mode == "hard"


def test_detected_core_fields() -> None:
    """레지스트리 밖 core 필드(거래유형·매매가)도 감지로 표면화."""
    parsed = _parse({"hard": {"deal_type": "jeonse", "deposit_max": 50000}})
    keys = {(d.criterion_key, d.mode) for d in parsed.detected}
    assert ("deal_type", "hard") in keys
    assert ("deposit", "hard") in keys


# ───────────────────────── JSON 추출 관용 ─────────────────────────


def test_extracts_json_in_code_fence() -> None:
    """코드펜스·머리말로 감싼 출력에서도 JSON 객체 추출."""
    text = '설명입니다.\n```json\n{"hard": {"has_daycare": true}, "soft": {}}\n```\n끝.'
    parsed = parse_query("질의", runner=_raw_runner(text))
    assert parsed.spec.has_daycare is True


def test_empty_output_raises() -> None:
    with pytest.raises(QueryParseError):
        parse_query("질의", runner=_raw_runner(""))


def test_non_json_output_raises() -> None:
    with pytest.raises(QueryParseError):
        parse_query("질의", runner=_raw_runner("죄송하지만 도울 수 없습니다."))


def test_incoherent_range_raises() -> None:
    """min>max 모순은 QueryParseError(엔드포인트 422). 모호와 달리 명시적 모순."""
    with pytest.raises(QueryParseError):
        _parse({"hard": {"approval_year_min": 2020, "approval_year_max": 2010}})


# ───────────────────────── 프롬프트·러너 ─────────────────────────


def test_registry_catalog_contains_registered_keys() -> None:
    """카탈로그가 레지스트리 key를 모두 담는다 — 어휘 grounding(드리프트 0)."""
    catalog = registry_catalog()
    for key in REGISTRY:
        assert f"`{key}`" in catalog


def test_build_prompt_injects_catalog_and_query() -> None:
    prompt = build_parse_prompt("역세권 신축")
    assert "역세권 신축" in prompt
    assert "`subway_time`" in prompt
    assert "{REGISTRY_CATALOG}" not in prompt  # 치환 완료
    assert "{QUERY}" not in prompt


def test_default_runner_invokes_claude_keyless(monkeypatch: pytest.MonkeyPatch) -> None:
    """_default_runner는 `claude -p`를 호출 — 웹 도구 미승인(순수 텍스트→JSON), 키 불필요."""
    import app.search.nl_parse as nl

    captured: dict[str, list[str]] = {}

    class _Proc:
        stdout = "{}"

    def fake_run(argv: list[str], **kw: object) -> _Proc:
        captured["argv"] = argv
        return _Proc()

    monkeypatch.setattr(nl.subprocess, "run", fake_run)
    _default_runner("prompt", 2)
    argv = captured["argv"]
    assert argv[:2] == ["claude", "-p"]
    assert "--allowedTools" not in argv  # NL 파싱은 웹 불필요
