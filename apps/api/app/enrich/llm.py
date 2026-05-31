"""LLM 추출 인터페이스 (주입형) + Anthropic default 구현.

llm_fn: (system, user) → 구조화 dict. 게이트는 mock, 실 호출은 P1-2-live(ANTHROPIC_API_KEY).
default는 haiku-4-5(단지내-vs-상업 구별은 짧은 본문 분류라 저비용 모델로 충분) + structured
output. system 프롬프트는 안정적이라 prompt caching 대상(반복 추출 비용↓).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

# (system, user) → 구조화 dict. 키: has_gym('yes'|'no'|'unknown'), in_complex(bool),
# evidence(str 요지), confidence(0..1).
LlmFn = Callable[[str, str], dict[str, Any]]

# 구조화 추출은 짧은 본문 분류라 저비용 모델로 충분(의뢰서 DEBATE 결정).
DEFAULT_MODEL = "claude-haiku-4-5"

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "has_gym": {"type": "string", "enum": ["yes", "no", "unknown"]},
        "in_complex": {"type": "boolean"},
        "evidence": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["has_gym", "in_complex", "evidence", "confidence"],
    "additionalProperties": False,
}


def build_anthropic_llm(api_key: str, *, model: str = DEFAULT_MODEL) -> LlmFn:
    """Anthropic 클라이언트를 닫은 LlmFn 반환. 실 추출(P1-2-live)에서만 사용.

    structured output(output_config.format)로 스키마를 강제하고, 안정적 system은
    prompt caching으로 반복 비용을 낮춘다. thinking 불요(단순 분류).
    """
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    def _call(system: str, user: str) -> dict[str, Any]:
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": _OUTPUT_SCHEMA}},
        )
        text = next((b.text for b in response.content if b.type == "text"), "{}")
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}

    return _call
