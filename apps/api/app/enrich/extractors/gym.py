"""헬스장 실추출기 (E1) — 비아파트는 K-apt amenities_raw가 없어 웹/POI로 보강.

웹/지도 POI("단지명 헬스장/피트니스") → provider-LLM → 출처별 {has_gym, evidence} fact.
상태 도메인 {yes,no,unknown} 강제(밖이면 unknown 보수). gym.py synthesize_gym가 카드로 합성.
graceful-degrade(소스/provider 실패 → defer). runner.enrich의 Extractor seam에 꽂힌다.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from app.enrich.extractors._common import clamp_confidence, parse_items, run_extraction
from app.enrich.fetcher import SourceDoc, SourceFetcher
from app.enrich.provider import LLMProvider
from app.enrich.runner import Extractor
from app.enrich.store import EnrichmentFact

ATTRIBUTE = "gym"
_STATES = {"yes", "no", "unknown"}

SYSTEM = (
    "너는 부동산 단지 부대시설 조사원이다. 주어진 소스들로 이 단지(건물)에 "
    "헬스장/피트니스가 있는지 판정한다. 소스에 근거 없으면 추측하지 말고 unknown. "
    "**소스가 이 단지와 무관하면(다른 건물·호텔 등) 'no'가 아니라 unknown** "
    "('no'는 헬스장 없음의 명시 근거가 있을 때만). 각 소스별로 JSON 배열의 한 객체로 답하라: "
    '{"source_url": <소스 url 그대로>, "has_gym": "yes"|"no"|"unknown", '
    '"evidence": <한 줄 근거>, "confidence": 0..1}. JSON 배열만 출력.'
)


def _parse(raw: str, by_url: dict[str, SourceDoc]) -> list[EnrichmentFact]:
    facts: list[EnrichmentFact] = []
    for it in parse_items(raw, by_url):
        state = it.get("has_gym")
        has_gym = state if state in _STATES else "unknown"  # 도메인 강제(보수)
        url = it["source_url"]
        value = json.dumps(
            {"has_gym": has_gym, "evidence": str(it.get("evidence") or "")}, ensure_ascii=False
        )
        facts.append(
            EnrichmentFact(
                value=value,
                confidence=clamp_confidence(it.get("confidence")),
                source_type=by_url[url].source_type,
                source_url=url,
            )
        )
    return facts


def make_gym_extractor(
    provider: LLMProvider, fetcher: SourceFetcher, name_of: Callable[[str], str | None]
) -> Extractor:
    """live gym 추출기 — (complex_id, attribute) → facts. name_of로 단지명 사전해소(스레드안전).

    runner.enrich가 miss 후보에 병렬 호출. name_of는 라이브 와이어링이 DB에서 미리 해소한 맵
    (스레드에서 conn 미사용). provider/fetcher 실패는 _common이 graceful 처리(defer).
    """

    def extract(complex_id: str, attribute: str) -> list[EnrichmentFact]:
        name = name_of(complex_id)
        if not name:
            return []
        return run_extraction(
            name,
            queries=[f"{name} 헬스장", f"{name} 피트니스"],
            kind="web",
            fetcher=fetcher,
            provider=provider,
            system=SYSTEM,
            parse=_parse,
        )

    return extract


__all__ = ["ATTRIBUTE", "make_gym_extractor"]
