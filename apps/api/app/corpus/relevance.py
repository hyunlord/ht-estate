"""rag-corpus-quality: 코퍼스 적재 품질 게이트 — 건물검증 + 후기-vs-노이즈 필터(룰 + 선택 LLM).

진단(diag-rag-state): 적재가 이름유사만으로 **딴 건물(해운대 케이씨씨스위첸)·딴 단지(파라곤)·경매·
인테리어·대출 광고**를 담음. 이 모듈이 **doc 단위**로 거른다(청크는 단락이라 단지명 없을 수 있어
doc 전체로 판정):
- **건물검증**: doc 텍스트가 **단지명 코어**(정규화·generic 접미 제거) + **지역(dong/sigungu)** 둘
  다 포함해야 keep. 이름만 매치(스위첸·파라곤)·딴 지역 → reject.
- **노이즈 필터**: 경매/인테리어/시공/대출/매물/분양/신고가 등 비-거주후기 → drop.
- **선택 LLM**: 룰 통과분을 gemma로 "이 텍스트가 <지역> <단지> 거주 후기냐?" 재확인(경계 precision).
보수적(precision>recall) — 오염 청크 1개가 잘못된 인용을 만든다.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from app.enrich.fetcher import SourceDoc
from app.enrich.provider import LLMProvider, ProviderError
from app.match.normalize import normalize_name

# 비-거주후기 노이즈 — 명백한 것만(보수적 drop). 경매/매물/시공/대출/광고.
_NOISE_KEYWORDS = (
    "경매", "입찰", "감정가", "낙찰", "유찰",
    "인테리어", "리모델링", "시공", "샷시", "새시", "도배", "철거",
    "담보대출", "후순위", "대출", "분양권", "분양", "매물", "신고가", "복비",
)
# 단지명 끝의 generic 건물유형 접미만(매칭 코어서 제거 — "케이씨씨엠파이어타워"→"케이씨씨엠파이어").
# 브랜드 토큰(스테이트/시티/파크 — 힐스테이트·자이) 미포함: 정체성이라 제거하면 오매칭.
_GENERIC_SUFFIX = re.compile(r"(타워|오피스텔|아파트|빌딩|빌라|주상복합)$")

# doc 분류기: (text) → keep/reject. 선택(미주입이면 룰만). gemma 등 주입(경계 precision).
ChunkClassifier = Callable[[str], bool]


def _norm(s: str | None) -> str:
    return normalize_name(s or "")


def building_name_core(name: str | None) -> str:
    """매칭용 단지명 코어 — 정규화 후 generic 접미 제거. 비면 정규화 전체."""
    n = _norm(name)
    core = _GENERIC_SUFFIX.sub("", n)
    return core or n


def region_tokens(sigungu: str | None, dong: str | None) -> list[str]:
    """지역 매칭 토큰 — dong(가장 변별적)·sigungu. 둘 다 없으면 빈(게이트 region 조건 무력화)."""
    return [t for t in (dong, sigungu) if t]


def doc_building_relevant(text: str, name: str, region_toks: list[str]) -> bool:
    """doc이 이 단지를 가리키나 — 단지명 코어 포함 + (지역토큰 있으면) 지역 포함. 보수적.

    region_toks 비면(지역 미상) 이름만으로 판정(과잉 reject 방지). 있으면 이름+지역 둘 다 요구.
    """
    nt = _norm(text)
    core = building_name_core(name)
    if not core or core not in nt:
        return False  # 단지명 코어 미포함 → 딴 건물(스위첸·파라곤) reject
    if region_toks and not any(_norm(r) in nt for r in region_toks):
        return False  # 지역 불일치(해운대 등) reject
    return True


def doc_is_noise(text: str) -> bool:
    """비-거주후기(경매·인테리어·대출·매물·광고) — 보수적 drop."""
    return any(k in text for k in _NOISE_KEYWORDS)


def filter_docs(
    docs: list[SourceDoc],
    *,
    name: str,
    region_toks: list[str],
    classifier: ChunkClassifier | None = None,
) -> list[SourceDoc]:
    """적재 전 doc 필터 — 건물검증 + 노이즈 + (선택)LLM. 통과 doc만 청킹/임베딩."""
    kept: list[SourceDoc] = []
    for doc in docs:
        if not doc_building_relevant(doc.text, name, region_toks):
            continue
        if doc_is_noise(doc.text):
            continue
        if classifier is not None and not classifier(doc.text):
            continue  # LLM 경계 reject(precision)
        kept.append(doc)
    return kept


# gemma doc 분류기 — 룰 통과분만 도달. "이 텍스트가 <지역> <단지> 거주 후기냐?" yes/no. 보수적이되
# LLM down이면 keep(룰 통과분 전량 reject 방지·graceful). 경계(개발기사·동명 타지) precision 보강.
_CLF_SYSTEM = (
    "너는 한국 아파트 후기 분류기다. 주어진 텍스트가 특정 단지의 '거주·생활 경험 후기/언급'인지 "
    "판정해라. 경매·매물·분양·담보대출·인테리어/시공/재개발 기사거나 다른 단지 얘기면 아니다. "
    "반드시 'yes' 또는 'no' 한 단어로만 답해라."
)


def make_doc_classifier(
    provider: LLMProvider | None, name: str, region_label: str
) -> ChunkClassifier | None:
    """단지별 gemma doc 분류기 — provider 없으면 None(룰만). LLM 실패는 keep(graceful).

    bulk 러너와 OnDemandCorpus가 공유 — 적재 경로(스크립트·detail 트리거) 정밀도 동형 보장.
    """
    if provider is None:
        return None

    def classify(text: str) -> bool:
        prompt = (f"단지: {region_label} {name}\n텍스트: {text}\n"
                  f"이 텍스트가 이 단지의 거주 후기/언급이냐? yes/no")
        try:
            ans = provider.complete(_CLF_SYSTEM, prompt)
        except ProviderError:
            return True  # LLM down → 룰 통과분 보존
        return "yes" in ans.strip().lower()[:8]

    return classify
