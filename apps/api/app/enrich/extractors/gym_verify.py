"""gym 증거 doc 교차검증 (gym-evidence) — Kakao 위치 신호 너머 "진짜 이 건물 헬스장이냐" 문서검증.

C84 Kakao 위치(gym_kakao·source_type='kakao_local')와 별개로, gym 쿼리 문서검색을 **C86 건물검증
게이트**(relevance.filter_docs: 단지명 코어+region 강등+노이즈 drop)로 거른 뒤 **gemma 구조화 판정**
(confirmed/no/unclear+증거 인용+confidence)해 **source_type='web_verified'** gym 증거로 저장한다.
synthesize_gym이 두 신호(kakao_local 위치 + web_verified 증거)를 **결합**(위치+증거·둘 다 표시).

별도 속성 `gym_verified`로 적재 — Kakao가 같은 'gym' 속성에 있어 has_fresh/readiness가 doc 검증을
단락시키는 걸 피한다(둘 다 확보). synthesize_gym가 'gym'+'gym_verified' 사실을 합쳐 결합 판정.
**재사용 패턴(후속 일반화)**: "doc 검색 → 건물게이트 → gemma 속성검증 → enrichment 증거"를
attribute/queries/verify_system 파라미터로 일반화. 지금은 gym만 인스턴스화(pet/소음 등은 후속).
graceful-degrade(소스/provider 실패·게이트 전부 reject → defer). 핫패스 밖(온디맨드 디테일·C53).
"""

from __future__ import annotations

import json
from collections.abc import Callable

from app.corpus.relevance import filter_docs
from app.enrich.extractors._common import clamp_confidence, parse_items, run_extraction
from app.enrich.fetcher import SourceDoc, SourceFetcher
from app.enrich.provider import LLMProvider
from app.enrich.runner import Extractor
from app.enrich.store import EnrichmentFact

# doc-LLM 검증 적재 속성/소스 — synthesize_gym이 'gym'(kakao_local) + 이것(web_verified)을 결합.
GYM_VERIFIED = "gym_verified"
WEB_VERIFIED = "web_verified"

# gemma 판정 → has_gym(confirmed=이 단지 헬스장 확인·no=없음 명시·unclear=불명/딴건물/추측).
_VERDICT = {"confirmed": "yes", "no": "no", "unclear": "unknown"}

# (name, region_label, region_tokens) — 메인 스레드 사전해소(스레드안전·게이트+검증 프롬프트 입력).
GymTarget = tuple[str | None, str, list[str]]

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
    provider: LLMProvider, fetcher: SourceFetcher, resolve: Callable[[str], GymTarget]
) -> Extractor:
    """live gym 증거 검증 추출기 — (complex_id, 'gym_verified') → web_verified facts.

    resolve(cid) → (name, region_label, region_tokens). gym 쿼리 문서검색 → C86 건물게이트로 딴
    건물·광고 drop → gemma 구조화 검증. graceful(defer). attribute는 'gym_verified' 고정 적재.
    """

    def extract(complex_id: str, _attribute: str) -> list[EnrichmentFact]:
        name, _region_label, region_toks = resolve(complex_id)
        if not name:
            return []

        def gate(docs: list[SourceDoc]) -> list[SourceDoc]:
            # C86 건물게이트 재사용(단지명 코어+region 강등+노이즈). 속성 디스앰비(이 건물 헬스장)는
            # 아래 gemma 검증이 직접 수행 → classifier 미사용(이중 LLM 회피).
            return filter_docs(docs, name=name, region_toks=region_toks)

        return run_extraction(
            name,
            queries=[f"{name} 헬스장", f"{name} 피트니스"],
            kind="web",
            fetcher=fetcher,
            provider=provider,
            system=SYSTEM,
            parse=_parse,
            doc_filter=gate,
        )

    return extract


__all__ = ["GYM_VERIFIED", "WEB_VERIFIED", "GymTarget", "make_gym_verify_extractor"]
