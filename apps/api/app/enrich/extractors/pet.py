"""강아지(반려동물) 실추출기 (E1) — **advisory**(설계 §11 "가장 약한 고리").

관리규약·카페·블로그 → provider-LLM → 출처별 {pet_allowed, evidence, caveats,
confirm_with_office} fact. 규율: **보수적**(근거 약하면 conditional/unknown, 거짓 'yes' 금지)·
**confirm_with_office 항상 true**(관리사무소 확인 권장 전수)·견종/무게/마릿수 caveats 보존·
출처 전부 보관. pet.py synthesize_pet가 카드로 합성(확인 배지 표면화). graceful-degrade.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from app.enrich.extractors._common import clamp_confidence, parse_items, run_extraction
from app.enrich.fetcher import SourceDoc, SourceFetcher
from app.enrich.provider import LLMProvider
from app.enrich.runner import Extractor
from app.enrich.store import EnrichmentFact

ATTRIBUTE = "pet"
_STATES = {"yes", "conditional", "no", "unknown"}

SYSTEM = (
    "너는 신중한 부동산 조사원이다. 주어진 소스로 이 단지의 반려동물(강아지) 허용 여부를 "
    "**보수적으로** 판정한다. 규칙: (1) 관리규약 등 명확한 근거 없이 'yes' 단정 금지 — 애매하면 "
    "conditional 또는 unknown. (2) 제한(견종·무게·마릿수)이 보이면 caveats에 담아라. (3) 소스에 "
    "없는 내용 추측 금지. 각 소스별 JSON 배열의 한 객체로: "
    '{"source_url": <url 그대로>, "pet_allowed": "yes"|"conditional"|"no"|"unknown", '
    '"evidence": <한 줄 근거>, "caveats": [<제한 단서>...], "confidence": 0..1}. JSON 배열만 출력.'
)


def _parse(raw: str, by_url: dict[str, SourceDoc]) -> list[EnrichmentFact]:
    facts: list[EnrichmentFact] = []
    for it in parse_items(raw, by_url):
        state = it.get("pet_allowed")
        pet = state if state in _STATES else "unknown"  # 도메인 강제(보수)
        raw_caveats = it.get("caveats")
        caveats = [str(c) for c in raw_caveats] if isinstance(raw_caveats, list) else []
        url = it["source_url"]
        value = json.dumps(
            {
                "pet_allowed": pet,
                "evidence": str(it.get("evidence") or ""),
                "caveats": caveats,
                "confirm_with_office": True,  # advisory — 관리사무소 확인 권장 전수(LLM 무관 강제)
            },
            ensure_ascii=False,
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


def make_pet_extractor(
    provider: LLMProvider, fetcher: SourceFetcher, name_of: Callable[[str], str | None]
) -> Extractor:
    """live pet 추출기(advisory) — (complex_id, attribute) → facts. name_of로 단지명 사전해소."""

    def extract(complex_id: str, attribute: str) -> list[EnrichmentFact]:
        name = name_of(complex_id)
        if not name:
            return []
        return run_extraction(
            name,
            queries=[f"{name} 반려동물", f"{name} 강아지 관리규약"],
            kind="web",
            fetcher=fetcher,
            provider=provider,
            system=SYSTEM,
            parse=_parse,
        )

    return extract


__all__ = ["ATTRIBUTE", "make_pet_extractor"]
