"""평판 retrieval+rerank+종합 (E3-3) — 질의 embed→KNN(complex 필터)→rerank→gemma 요약+인용.

읽기 측 코어(canon write 0 → 지문/counts 불변). 3 모델 의존(embed·rerank·gemma) 각각 **graceful
degrade**(멀티테넌트 — 다 killable·crash 0):
  · embed down(EmbedUnavailable) → 질의 임베딩 불가 → retrieve 불가 → **PENDING**(다음 재시도).
  · rerank down(RerankUnavailable) → **KNN 순서 fallback**(rerank 없이 진행·degraded='rerank').
  · gemma/synth down(ProviderError) → **인용만**(evidence-only·summary=None·degraded+='synth').
**DB권 경계 + advisory**: 종합은 요약+전언체·단정 금지·상충 노출·원문 대량 재현 금지(길이 가드).
인용은 source_url + span_ref(딥링크 정밀). 키리스: embed/rerank/provider mock 주입.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.corpus.store import ReviewChunk, read_chunks_by_ids
from app.corpus.vec import knn_filtered
from app.embed.client import Embedder, EmbedUnavailable, Reranker, RerankUnavailable
from app.enrich.provider import LLMProvider, ProviderError

# 상태 — gym/pet 동형(ready/pending/unavailable).
READY = "ready"
PENDING = "pending"
UNAVAILABLE = "unavailable"

DEFAULT_TOP_K = 12   # KNN 후보(단지 내)
DEFAULT_TOP_N = 5    # rerank 후 종합 입력
SUMMARY_MAX_CHARS = 600  # DB권 길이 가드(원문 대량 재현 방지)

SYNTH_SYSTEM = (
    "너는 부동산 단지 '평판 요약가'다. 주어진 후기 발췌들로 이 단지에 대해 **회자되는 평판**을 "
    "요약한다. 규칙: (1) 발췌에 근거한 내용만 — 추측·단정 금지. (2) 상충하는 의견이 있으면 "
    "양쪽 다 노출. (3) 원문을 길게 옮기지 말고 요약(전언체: '~라는 평', '~라고 언급됨'). "
    "(4) 2~4문장. (5) 확정 단언 금지(후기는 주관적·확인 권장). 한국어로 답하라."
)


@dataclass(frozen=True)
class Citation:
    """인용 1건 — 종합 근거 발췌 + 딥링크 정밀(source_url + span_ref)."""

    source_type: str
    source_url: str
    span_ref: str | None
    snippet: str  # 발췌 본문(evidence-only fallback·UI 근거)


@dataclass(frozen=True)
class ReputationResult:
    """평판 종합 결과 — status + 요약(degrade 시 None) + 인용 + degraded 표시(투명성)."""

    status: str
    summary: str | None = None
    citations: list[Citation] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)  # 어떤 모델이 degrade했는지(투명성)


def _build_user_prompt(query: str, chunks: list[ReviewChunk]) -> str:
    lines = [f"질의: {query}", "", "후기 발췌:"]
    for i, c in enumerate(chunks, 1):
        lines.append(f"[{i}] ({c.source_type}) {c.chunk_text}")
    lines.append("")
    lines.append("위 발췌만 근거로 회자되는 평판을 요약하라(단정 금지·상충 노출·전언체).")
    return "\n".join(lines)


def synthesize_reputation(
    conn,  # sqlite3.Connection
    complex_id: str,
    query: str,
    *,
    embed_client: Embedder,
    rerank_client: Reranker,
    provider: LLMProvider | None,
    top_k: int = DEFAULT_TOP_K,
    top_n: int = DEFAULT_TOP_N,
) -> ReputationResult:
    """질의→embed→단지필터 KNN→rerank→gemma 종합. 코퍼스 신선 가정(호출부가 OnDemand로 보장).

    3 모델 각각 graceful degrade. 반환 status: READY(요약 또는 인용만) / PENDING(embed down).
    """
    # 1) 질의 embed (down → 검색 불가 → PENDING)
    try:
        qvec = embed_client.embed([query]).vectors[0]
    except EmbedUnavailable:
        return ReputationResult(PENDING, degraded=["embed"])

    # 2) 단지-필터 KNN top-k
    hits = knn_filtered(conn, complex_id, qvec, top_k)
    if not hits:
        return ReputationResult(READY, summary=None, citations=[])  # 코퍼스 있으나 매치 0
    by_id = read_chunks_by_ids(conn, [cid for cid, _ in hits])
    candidates = [by_id[cid] for cid, _ in hits if cid in by_id]
    if not candidates:
        return ReputationResult(READY, summary=None, citations=[])

    # 3) rerank (down → KNN 순서 fallback)
    degraded: list[str] = []
    try:
        ranked_hits = rerank_client.rerank(query, [c.chunk_text for c in candidates], top_n=top_n)
        ranked = [candidates[h.index] for h in ranked_hits if 0 <= h.index < len(candidates)]
    except RerankUnavailable:
        ranked = candidates[:top_n]
        degraded.append("rerank")
    if not ranked:
        ranked = candidates[:top_n]

    citations = [
        Citation(c.source_type, c.source_url, c.span_ref, c.chunk_text) for c in ranked
    ]

    # 4) gemma 종합 (down/미구성/빈응답 → 인용만·evidence-only)
    def _evidence_only() -> ReputationResult:
        return ReputationResult(READY, None, citations, degraded=[*degraded, "synth"])

    if provider is None:
        return _evidence_only()
    try:
        raw = provider.complete(SYNTH_SYSTEM, _build_user_prompt(query, ranked))
    except ProviderError:
        return _evidence_only()
    summary = (raw or "").strip()[:SUMMARY_MAX_CHARS]  # DB권 길이 가드
    if not summary:
        return _evidence_only()
    return ReputationResult(READY, summary=summary, citations=citations, degraded=degraded)
