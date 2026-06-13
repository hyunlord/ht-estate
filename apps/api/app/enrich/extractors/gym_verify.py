"""gym 증거 doc 교차검증 (gym-evidence) — 공유 doc_verify 패턴의 **thin config**(헬스장).

C84 Kakao 위치(gym_kakao·kakao_local) 너머 "진짜 이 건물 헬스장이냐"를 문서로 검증한다.
코어 파이프라인(fetch→C86 건물게이트→gemma→web_verified 사실)은 doc_verify.make_doc_verify_extractor
가 제공 — 여긴 gym 고유 config(쿼리·verify_system·verdict 파서)만. synthesize_gym이 Kakao 위치 +
이 doc 검증(web_verified)을 **결합**. 별도 속성 'gym_verified'(Kakao가 트리거 단락 회피).
graceful·온디맨드 핫패스 밖은 공유 코어가 보장.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from app.enrich.extractors._common import clamp_confidence, parse_items
from app.enrich.extractors.doc_verify import (
    WEB_VERIFIED,
    DocTarget,
    make_doc_verify_extractor,
)
from app.enrich.fetcher import SourceDoc, SourceFetcher
from app.enrich.provider import LLMProvider
from app.enrich.runner import Extractor
from app.enrich.store import EnrichmentFact

# doc-LLM 검증 적재 속성 — synthesize_gym이 'gym'(kakao_local) + 이것(web_verified) 결합.
GYM_VERIFIED = "gym_verified"
GymTarget = DocTarget  # 하위호환 별칭

# gemma 판정 → has_gym(confirmed=이 단지 헬스장 확인·no=없음 명시·unclear=불명/딴건물/추측).
_VERDICT = {"confirmed": "yes", "no": "no", "unclear": "unknown"}

SYSTEM = (
    "너는 부동산 단지 부대시설 검증원이다. 주어진 소스들로 이 단지(건물)에 헬스장/피트니스가 "
    "실제로 있는지 교차검증한다. 각 소스가 '이 단지 건물 내/단지 시설로서의 헬스장'을 확인하는지 "
    "판정: confirmed(이 단지 헬스장 확인) / no(없음 명시) / unclear(불명·딴 건물·추측). "
    "근거 없으면 unclear. 소스별 JSON 배열의 한 객체로: {\"source_url\": <소스 url 그대로>, "
    '"verdict": "confirmed"|"no"|"unclear", "evidence": <한 줄 인용>, "confidence": 0..1}. '
    "JSON 배열만 출력."
)


def _parse(raw: str, by_url: dict[str, SourceDoc]) -> list[EnrichmentFact]:
    facts: list[EnrichmentFact] = []
    for it in parse_items(raw, by_url):
        has_gym = _VERDICT.get(str(it.get("verdict") or ""), "unknown")  # 도메인 강제(보수)
        url = it["source_url"]
        value = json.dumps(
            {"has_gym": has_gym, "evidence": str(it.get("evidence") or "")}, ensure_ascii=False
        )
        facts.append(
            EnrichmentFact(
                value=value,
                confidence=clamp_confidence(it.get("confidence")),
                source_type=WEB_VERIFIED,  # doc-LLM 검증 — 결합서 kakao_local과 구분
                source_url=url,
            )
        )
    return facts


def make_gym_verify_extractor(
    provider: LLMProvider, fetcher: SourceFetcher, resolve: Callable[[str], DocTarget]
) -> Extractor:
    """live gym 증거 검증 추출기 — 공유 doc_verify 패턴 + gym thin config."""
    return make_doc_verify_extractor(
        provider, fetcher, resolve,
        queries_fn=lambda name: [f"{name} 헬스장", f"{name} 피트니스"],
        verify_system=SYSTEM,
        parse=_parse,
    )

__all__ = ["GYM_VERIFIED", "WEB_VERIFIED", "GymTarget", "make_gym_verify_extractor"]
