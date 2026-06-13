"""구조화 속성 doc 교차검증 — 공유 패턴 (gym-evidence→일반화 pet-evidence).

C87 gym-evidence가 만든 검증 파이프라인을 **속성-불가지(attribute-agnostic)** 모듈로 추출한다:
  doc 검색(쿼리) → **C86 건물검증 게이트**(relevance.filter_docs: 단지명 코어+region 강등+노이즈)
  → **gemma 구조화 판정**(속성별 verify_system) → source_type='web_verified' 사실.
구조화 속성 추가 = 이 위에 **thin config**(queries_fn·verify_system·parse) 1개. review_chunk 미사용
(후기 RAG와 분리)·온디맨드 핫패스 밖. 인스턴스: gym_verify(헬스장)·pet_verify(반려동물·advisory).

== 다음 구조화 속성 추가법 (3-step thin config) ==
  1) `app/enrich/extractors/<attr>_verify.py`: <ATTR>_VERIFIED 속성명 + verify SYSTEM 프롬프트
     + _parse(verdict→상태 value·source_type=WEB_VERIFIED) + make_<attr>_verify_extractor =
     make_doc_verify_extractor(..., queries_fn, verify_system, parse) 래퍼.
  2) `live.py` live_extractors에 '<attr>_verified': make_<attr>_verify_extractor(.., resolver).
  3) `main.py` 엔드포인트: read_facts('<attr>') + status('<attr>_verified') → synthesize 결합.
  코어(fetch·게이트·gemma·write 격리·온디맨드·graceful)는 전부 재사용 — 속성 코드는 thin.
"""

from __future__ import annotations

from collections.abc import Callable

from app.corpus.relevance import filter_docs
from app.enrich.extractors._common import run_extraction
from app.enrich.fetcher import SourceDoc, SourceFetcher
from app.enrich.provider import LLMProvider
from app.enrich.runner import Extractor
from app.enrich.store import EnrichmentFact

# doc-LLM 검증 적재 소스 — synthesize 결합이 kakao_local 등 타 신호와 구분(딥링크 provenance).
WEB_VERIFIED = "web_verified"

# (name, region_label, region_tokens) — 메인 스레드 사전해소(스레드안전·게이트+검증 프롬프트 입력).
DocTarget = tuple[str | None, str, list[str]]

# 속성별 파서: (raw, by_url) → facts. verdict→상태 value·source_type=WEB_VERIFIED는 인스턴스가 빌드.
DocParse = Callable[[str, dict[str, SourceDoc]], list[EnrichmentFact]]


def make_doc_verify_extractor(
    provider: LLMProvider,
    fetcher: SourceFetcher,
    resolve: Callable[[str], DocTarget],
    *,
    queries_fn: Callable[[str], list[str]],
    verify_system: str,
    parse: DocParse,
) -> Extractor:
    """공유 doc 검증 추출기 — (complex_id, attribute) → web_verified facts. 속성은 thin config.

    resolve(cid) → (name, region_label, region_tokens). queries_fn(name) → 검색 쿼리(속성별).
    fetch → C86 건물게이트(filter_docs: 코어+region 강등+노이즈) → gemma(verify_system) 구조화 판정.
    속성 디스앰비("이 건물의 <속성>이냐")는 verify_system이 직접 수행 → 게이트 classifier 미사용.
    graceful(소스/provider 실패·게이트 전부 reject → defer·crash 0).
    """

    def extract(complex_id: str, _attribute: str) -> list[EnrichmentFact]:
        name, _region_label, region_toks = resolve(complex_id)
        if not name:
            return []

        def gate(docs: list[SourceDoc]) -> list[SourceDoc]:
            return filter_docs(docs, name=name, region_toks=region_toks)

        return run_extraction(
            name,
            queries=queries_fn(name),
            kind="web",
            fetcher=fetcher,
            provider=provider,
            system=verify_system,
            parse=parse,
            doc_filter=gate,
        )

    return extract


__all__ = ["WEB_VERIFIED", "DocTarget", "DocParse", "make_doc_verify_extractor"]
