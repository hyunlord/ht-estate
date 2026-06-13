"""pet(반려동물) 증거 doc 교차검증 (pet-evidence) — 공유 doc_verify 패턴 **thin config**.

C87 gym-evidence 패턴의 2번째 인스턴스(일반화 실증). 코어(fetch→C86 건물게이트→gemma→web_verified)는
doc_verify가 제공 — 여긴 pet 고유 config(쿼리·verify_system·verdict 파서)만.

★★ 안전(advisory) 바닥: 반려동물 허용은 관리규약 단위·세대/견종별 가변·잘못된 "가능"이 실제 피해
(분양받고 못 키움). → **하드 ✓ 금지**. allowed도 **항상 advisory**(confirm_with_office=True
전수·견종/무게 단서 caveats 보존). synthesize_pet 결합·DetailPanel pet 행이 advisory로 렌더(프론트가
definitive yes 안 함)·missing=keep(unclear/무사실→미확인). 별도 속성 'pet_verified'(기존 pet/시드가
트리거 단락 회피). graceful·온디맨드 핫패스 밖은 공유 코어가 보장.
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

# doc-LLM 검증 적재 속성 — synthesize_pet이 'pet'(시드/레거시) + 이것(web_verified) 결합.
PET_VERIFIED = "pet_verified"

# gemma 판정 → pet_allowed. allowed는 'yes'로 두되 **프론트가 advisory 렌더**(하드 ✓ 금지)·
# confirm_with_office 전수 true. not-allowed=no(명시 금지)·unclear=unknown(미확인·missing=keep).
_VERDICT = {"allowed": "yes", "not-allowed": "no", "unclear": "unknown"}

SYSTEM = (
    "너는 신중한 부동산 조사원이다. 주어진 소스로 이 단지가 관리규약상 반려동물(강아지)을 "
    "허용하는지 **보수적으로** 교차검증한다. 규칙: (1) 관리규약 등 명확한 근거 없이 allowed 단정 "
    "금지 — 애매하면 unclear. (2) 견종·무게·마릿수 제한이 보이면 caveats에 담아라. (3) 소스에 없는 "
    "내용 추측 금지. (4) **소스가 이 단지와 무관하면(딴 건물·펫샵/동물병원 광고 등) not-allowed가 "
    "아니라 unclear** — 무관함은 '불허'가 아니다(not-allowed는 명시 금지 근거 있을 때만). 소스별 "
    "JSON 배열의 한 객체로: {\"source_url\": <url 그대로>, "
    '"verdict": "allowed"|"not-allowed"|"unclear", "evidence": <한 줄 인용>, '
    '"caveats": [<견종/무게/마릿수 제한>...], "confidence": 0..1}. JSON 배열만 출력.'
)


def _parse(raw: str, by_url: dict[str, SourceDoc]) -> list[EnrichmentFact]:
    facts: list[EnrichmentFact] = []
    for it in parse_items(raw, by_url):
        pet_allowed = _VERDICT.get(str(it.get("verdict") or ""), "unknown")  # 도메인 강제(보수)
        raw_caveats = it.get("caveats")
        caveats = [str(c) for c in raw_caveats] if isinstance(raw_caveats, list) else []
        url = it["source_url"]
        value = json.dumps(
            {
                "pet_allowed": pet_allowed,
                "evidence": str(it.get("evidence") or ""),
                "caveats": caveats,
                "confirm_with_office": True,  # advisory — 관리사무소 확인 전수(LLM 무관 강제)
            },
            ensure_ascii=False,
        )
        facts.append(
            EnrichmentFact(
                value=value,
                confidence=clamp_confidence(it.get("confidence")),
                source_type=WEB_VERIFIED,  # doc-LLM 검증 — provenance 딥링크
                source_url=url,
            )
        )
    return facts


def make_pet_verify_extractor(
    provider: LLMProvider, fetcher: SourceFetcher, resolve: Callable[[str], DocTarget]
) -> Extractor:
    """live pet 증거 검증 추출기 — 공유 doc_verify 패턴 + pet thin config(advisory)."""
    return make_doc_verify_extractor(
        provider, fetcher, resolve,
        queries_fn=lambda name: [f"{name} 반려동물", f"{name} 강아지", f"{name} 애완동물"],
        verify_system=SYSTEM,
        parse=_parse,
    )


__all__ = ["PET_VERIFIED", "make_pet_verify_extractor"]
