"""대화형 에이전트 오케스트레이터 (E5-1) — claude -p 백본 위 grounded 합성. read-only.

#5 백엔드 phase A. 신규 /chat 엔드포인트의 핵심: (a)필터 스텝 (b)bounded grounding 수집
(c)엄격 ground-only 합성 (d)파싱/반환. **재사용(중복 0)**: nl_parse.parse_query(claude -p)·
search(_run_search: attach_*+rank)·reputation/RAG 엔진. 새 LLM 백본·새 검색·새 평판 0.

★ 환각방지 바닥: 합성 프롬프트는 '제공 데이터만·출처 인용·없으면 미수집' 엄격 지시 + referenced_
complexes는 **수집 후보의 실 id만**(답변서 언급된 것만·날조 drop). 무데이터 경로는 정직("미수집").
멀티턴: 매 콜에 history 스레딩(claude -p 무상태·엔드포인트 무상태). graceful: 평판 엔진 down →
구조화 후보 데이터로 답변(crash 0). bounded: top-N 후보·핵심 필드만(프롬프트 fit).

주입형(키리스 테스트): runner(ClaudeRunner=claude -p)·search_fn(_run_search)·reputation_fn(평판).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence

from pydantic import BaseModel

from app.reputation.service import READY as REP_READY
from app.reputation.service import ReputationResult
from app.search.nl_parse import ClaudeRunner, parse_query
from app.search.repo import Candidate
from app.search.spec import HardFilterSpec

# grounding bound — top-N 후보만 수집(프롬프트 fit·빠름/저비용). 평판 RAG는 더 좁게(비용 큼).
GROUNDING_TOP_N = 12
REPUTATION_TOP_N = 5

# 합성 시스템 지시 — ★ ground-only/cite/no-fabrication(환각방지 바닥·프롬프트로 단언).
SYNTH_SYSTEM = (
    "너는 부동산 단지 안내 에이전트다. 아래 '수집 데이터'에 있는 사실만 사용해 한국어로 답하라.\n"
    "규칙(엄수):\n"
    "1) 제공 데이터에 없는 사실은 **지어내지 말 것** — 없으면 '미수집'이라고 명시.\n"
    "2) 각 사실 주장에는 데이터에 동반된 **출처(source)**를 함께 밝힐 것.\n"
    "3) 단지는 데이터의 complex_id 또는 name으로 참조할 것.\n"
    "4) 추측·과장 금지. 대화 맥락(history)을 고려해 간결·자연스럽게 답할 것."
)

# 평판 fetcher: (conn, complex_id, query) → ReputationResult | None(미가용/실패 시 graceful None).
ReputationFn = Callable[[sqlite3.Connection, str, str], ReputationResult | None]
SearchFn = Callable[[sqlite3.Connection, HardFilterSpec], list[Candidate]]


class ChatTurn(BaseModel):
    """대화 한 턴 — 멀티턴 history(프론트가 보유·매 콜 스레딩). role='user'|'assistant'."""

    role: str
    content: str


class ChatCitation(BaseModel):
    """인용 1건 — 기존 provenance 재사용(enrichment / review_chunk source_url+span_ref)."""

    source_type: str
    source_url: str
    span_ref: str | None = None
    snippet: str | None = None


class ChatResult(BaseModel):
    """채팅 응답 — 근거 prose + 언급 단지 실id + 인용 + (필터 바뀌면)갱신 spec."""

    answer: str
    referenced_complexes: list[str]
    citations: list[ChatCitation]
    updated_spec: HardFilterSpec | None = None


_HARD_KEYS = frozenset({
    "deal_type", "property_type", "price_min", "price_max", "deposit_min", "deposit_max",
    "monthly_rent_min", "monthly_rent_max", "net_area_min", "net_area_max",
    "approval_year_min", "approval_year_max", "household_count_min", "household_count_max",
    "parking_ratio_gte", "parking_underground", "subway_max_dist_m", "elem_max_dist_m",
    "mid_max_dist_m", "high_max_dist_m", "mart_count_1km_min", "conv_count_1km_min",
    "hospital_max_dist_m", "pharmacy_max_dist_m", "park_max_dist_m", "elevator_count_min",
    "cctv_count_min", "top_floor_min", "heat_type", "builder", "assigned_school",
})


def _is_filter_message(parsed_spec: HardFilterSpec, detected: Sequence[object]) -> bool:
    """메시지가 '필터 변경'인가 — 하드 필터/지역(bbox)/하드 감지 중 하나라도 있으면 True.

    soft-only 정제("그 중 주차 넉넉한 데?")·열린 질문은 False → context 후보 재사용(헛 필터 0).
    """
    if parsed_spec.has_bbox:
        return True
    # exclude_defaults: deal_type='sale'·limit 등 기본값 제외 → 실제 설정된 하드 필드만 신호.
    dump = parsed_spec.model_dump(exclude_defaults=True)
    if _HARD_KEYS & dump.keys():
        return True
    return any(getattr(d, "mode", None) == "hard" for d in detected)


def _merge_bbox(spec: HardFilterSpec, context: HardFilterSpec | None) -> HardFilterSpec:
    """필터 메시지가 bbox를 안 정했으면 context의 현 지도 범위 상속(현 뷰 안에서 필터)."""
    if spec.has_bbox or context is None or not context.has_bbox:
        return spec
    return spec.model_copy(update={
        "min_lat": context.min_lat, "max_lat": context.max_lat,
        "min_lng": context.min_lng, "max_lng": context.max_lng,
    })


def _fmt_won(v: int | None) -> str:
    if v is None:
        return "미수집"
    return f"{v / 10000:.1f}억" if v >= 10000 else f"{v:,}만"


def _candidate_grounding(c: Candidate) -> tuple[str, list[ChatCitation]]:
    """후보 1건의 구조화 grounding 텍스트 + 인용(출처 동반). 핵심 필드만(bounded·프롬프트 fit).

    없는 값은 '미수집'으로 정직 표기(날조 0). gym/pet은 source_url 동반(인용).
    """
    cites: list[ChatCitation] = []
    lines = [f"- [{c.complex_id}] {c.name or '이름미상'}"]
    rt = c.representative_trade
    if rt is not None:
        lines.append(f"  · 최근 대표거래가: {_fmt_won(rt.price)} (출처: 실거래·MOLIT)")
    if c.parking_ratio is not None:
        lines.append(f"  · 세대당주차: {c.parking_ratio:.2f}대 (출처: K-apt 대장)")
    if c.household_count is not None:
        lines.append(f"  · 세대수: {c.household_count:,} (출처: K-apt 대장)")
    if c.approval_date:
        lines.append(f"  · 사용승인: {c.approval_date[:4]} (출처: K-apt 대장)")
    if c.gym is not None and c.gym.has_gym in ("yes", "no"):
        src = next((s.source_url for s in c.gym.sources if s.source_url), None)
        ev = c.gym.evidence or ""
        lines.append(f"  · 헬스장: {c.gym.has_gym} {ev} (출처: {src or 'enrichment'})")
        if src:
            cites.append(ChatCitation(source_type="enrichment", source_url=src,
                                      snippet=c.gym.evidence))
    if c.pet is not None and c.pet.pet_allowed in ("yes", "conditional", "no"):
        src = next((s.source_url for s in c.pet.sources if s.source_url), None)
        lines.append(
            f"  · 반려동물(advisory·관리사무소 확인): {c.pet.pet_allowed} "
            f"(출처: {src or 'enrichment'})")
        if src:
            cites.append(ChatCitation(source_type="enrichment", source_url=src,
                                      snippet=c.pet.evidence))
    return "\n".join(lines), cites


def run_chat(
    conn: sqlite3.Connection,
    *,
    message: str,
    history: Sequence[ChatTurn],
    context_spec: HardFilterSpec | None,
    runner: ClaudeRunner,
    search_fn: SearchFn,
    reputation_fn: ReputationFn,
    top_n: int = GROUNDING_TOP_N,
) -> ChatResult:
    """대화형 오케스트레이터 — (a)필터 (b)grounding (c)합성 (d)파싱/반환. read-only.

    runner=claude -p(parse+합성 공유)·search_fn=_run_search·reputation_fn=평판(graceful).
    """
    # (a) 필터 스텝 — parse_query 재사용. 하드/지역 함의면 갱신·search, 아니면 context 재사용.
    parsed = parse_query(message, runner=runner)
    updated_spec: HardFilterSpec | None = None
    if _is_filter_message(parsed.spec, parsed.detected):
        spec = _merge_bbox(parsed.spec, context_spec)
        updated_spec = spec
    else:
        spec = context_spec  # 후속/열린 질문 → 현 필터 후보(새 필터 날조 X)

    # 후보 — 새 필터든 context든 동일 search 경로(_run_search 재사용·중복 0). spec 없으면 후보 0.
    candidates = search_fn(conn, spec)[:top_n] if spec is not None else []

    # (b) grounding 수집(bounded) — 구조화 + (평판 의도 있으면)RAG. 후보 0이면 정직 무결과.
    cand_ids = {c.complex_id for c in candidates}
    cand_blocks: list[str] = []
    citations: list[ChatCitation] = []
    for c in candidates:
        block, cites = _candidate_grounding(c)
        cand_blocks.append(block)
        citations.extend(cites)

    # 평판 RAG grounding — 열린 리뷰/지역 질문(reputation_query 추출됨)일 때만·상위 소수만(bounded).
    rep_query = parsed.reputation_query or (message if not updated_spec else None)
    if rep_query:
        for c in candidates[:REPUTATION_TOP_N]:
            rep = reputation_fn(conn, c.complex_id, rep_query)  # graceful: 실패면 None
            if rep is None or rep.status != REP_READY:
                continue
            if rep.summary:
                cand_blocks.append(f"  · [{c.complex_id}] 평판요약: {rep.summary} (출처: 후기 RAG)")
            for cit in rep.citations[:2]:
                citations.append(ChatCitation(
                    source_type=cit.source_type, source_url=cit.source_url,
                    span_ref=cit.span_ref, snippet=cit.snippet))

    # (c) 합성 — history(멀티턴) + 수집 데이터(출처 동반) + 엄격 ground-only 지시 → claude -p.
    prompt = _build_prompt(message, history, cand_blocks)
    try:
        answer = runner(prompt, 2).strip()
    except Exception:  # noqa: BLE001 — 합성 LLM 실패도 graceful(crash 0)
        answer = "죄송합니다, 지금 답변 생성에 문제가 있어요. 잠시 후 다시 시도해 주세요."

    # (d) 파싱/반환 — referenced_complexes는 **수집 후보 실 id만**(답변 언급분·날조 drop).
    referenced = [
        c.complex_id for c in candidates
        if (c.complex_id in answer) or (c.name and c.name in answer)
    ]
    referenced = list(dict.fromkeys(referenced))  # 순서보존 dedup
    # 안전망: 혹시 모를 비-후보 id 혼입 0(실 id만) — 구성상 이미 cand에서만 뽑지만 명시 가드.
    referenced = [cid for cid in referenced if cid in cand_ids]
    return ChatResult(
        answer=answer, referenced_complexes=referenced,
        citations=_dedup_citations(citations), updated_spec=updated_spec,
    )


def _build_prompt(message: str, history: Sequence[ChatTurn], cand_blocks: Sequence[str]) -> str:
    """합성 프롬프트 — 시스템(ground-only) + history(멀티턴) + 수집 데이터 + 질문."""
    parts = [SYNTH_SYSTEM, ""]
    if history:
        parts.append("[대화 맥락]")
        parts.extend(f"{t.role}: {t.content}" for t in history)
        parts.append("")
    parts.append("[수집 데이터]")
    parts.append("\n".join(cand_blocks) if cand_blocks else "(수집된 후보 없음 — 무결과)")
    parts.append("")
    parts.append(f"[사용자 질문]\n{message}")
    return "\n".join(parts)


def _dedup_citations(cites: Sequence[ChatCitation]) -> list[ChatCitation]:
    seen: set[tuple[str, str | None]] = set()
    out: list[ChatCitation] = []
    for c in cites:
        key = (c.source_url, c.span_ref)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


__all__ = ["ChatTurn", "ChatCitation", "ChatResult", "run_chat", "SYNTH_SYSTEM"]
