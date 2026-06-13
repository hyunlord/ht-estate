"""대화형 에이전트 오케스트레이터 (E5-1) — grounded 합성·환각방지·멀티턴. 키리스(mock 러너).

검증: (a)필터 메시지→parse_query 재사용·spec 갱신·grounding (b)후속(필터無)→context 후보(헛 필터 0)
(c)합성 프롬프트=수집데이터+ground-only/cite 지시+history (d)referenced 실id만(날조 drop)·
무후보 정직 무결과·graceful(평판 down→구조화 답변·crash 0). canonical write 0(read-only).
"""

from __future__ import annotations

import json
import sqlite3

from app.chat.orchestrator import ChatTurn, run_chat
from app.reputation.service import READY as REP_READY
from app.reputation.service import Citation, ReputationResult
from app.search.enrichment import EnrichSource
from app.search.gym import GymSummary
from app.search.repo import Candidate, RepresentativeTrade
from app.search.spec import HardFilterSpec

_CONN = sqlite3.connect(":memory:")


def _cand(
    cid: str, name: str, *, price: int | None = None, gym: GymSummary | None = None
) -> Candidate:
    rt = (RepresentativeTrade(net_area=84.0, price=price, floor=10, deal_date="2026-05-01",
                              match_confidence=1.0) if price is not None else None)
    return Candidate(
        complex_id=cid, name=name, approval_date="2020-01-01", parking_ratio=1.5,
        parking_underground=100, household_count=300, lat=37.5, lng=127.0, source_url=None,
        transaction_count=1, price_min=price, price_max=price, representative_trade=rt, gym=gym,
    )


def _gym_yes(url: str = "https://blog.naver.com/x/1") -> GymSummary:
    return GymSummary(has_gym="yes", confidence=0.88, evidence="단지 내 헬스장",
                      sources=[EnrichSource(source_type="kakao_local", source_url=url)])


class MockRunner:
    """claude -p mock — parse 프롬프트엔 spec JSON, 합성 프롬프트엔 prose. 호출 프롬프트 기록."""

    def __init__(self, spec_payload: dict, prose: str) -> None:
        self.spec_json = json.dumps(spec_payload, ensure_ascii=False)
        self.prose = prose
        self.prompts: list[str] = []

    def __call__(self, prompt: str, max_turns: int) -> str:
        self.prompts.append(prompt)
        if "안내 에이전트" in prompt:  # 합성(SYNTH_SYSTEM) 프롬프트
            return self.prose
        return self.spec_json  # parse_query 프롬프트


def _search_returns(cands: list[Candidate]):  # type: ignore[no-untyped-def]
    return lambda _conn, _spec: list(cands)


def _no_rep(_c, _cid, _q):  # type: ignore[no-untyped-def]
    return None


# ── (a) 필터 메시지 → parse_query 재사용·spec 갱신·후보 grounding ──
def test_filter_message_updates_spec_and_grounds_candidates() -> None:
    cands = [_cand("of:1", "강남오피스텔A", price=50000, gym=_gym_yes()),
             _cand("of:2", "강남오피스텔B", price=60000)]
    runner = MockRunner({"hard": {"property_type": "officetel"}},
                        "강남오피스텔A는 헬스장이 있습니다.")
    r = run_chat(_CONN, message="강남 오피스텔 헬스장", history=[], context_spec=None,
                 runner=runner, search_fn=_search_returns(cands), reputation_fn=_no_rep)
    assert r.updated_spec is not None  # parse_query 재사용
    assert r.updated_spec.property_type == "officetel"
    assert "of:1" in r.referenced_complexes  # 답변 언급 단지(실 id)
    assert any(c.source_url == "https://blog.naver.com/x/1" for c in r.citations)  # 인용


# ── (b) 후속(필터 無) → context 후보 grounding·헛 필터 0 ──
def test_followup_reuses_context_candidates_no_new_filter() -> None:
    cands = [_cand("A1", "역삼래미안", price=142000)]
    runner = MockRunner({"reputation_query": "주차 넉넉"},  # 하드 필터 0 → 후속
                        "역삼래미안 주차가 넉넉합니다.")
    ctx = HardFilterSpec.model_validate(
        {"min_lat": 37.4, "max_lat": 37.6, "min_lng": 127.0, "max_lng": 127.1})
    captured: list = []
    def search_fn(_conn, spec):  # type: ignore[no-untyped-def]
        captured.append(spec)
        return list(cands)
    r = run_chat(_CONN, message="그 중 주차 제일 넉넉한 데는?", history=[], context_spec=ctx,
                 runner=runner, search_fn=search_fn, reputation_fn=_no_rep)
    assert r.updated_spec is None  # 새 필터 날조 0
    assert captured[0] is ctx  # context 후보 재사용(헛 필터 0)
    assert r.referenced_complexes == ["A1"]


# ── (c) 합성 프롬프트 = 수집 데이터 + 엄격 ground-only/cite 지시 + history ──
def test_synthesis_prompt_has_ground_only_instruction_and_data_and_history() -> None:
    cands = [_cand("A1", "역삼래미안", price=142000)]
    runner = MockRunner({"hard": {"property_type": "apartment"}}, "답변")
    hist = [ChatTurn(role="user", content="이전 질문"),
            ChatTurn(role="assistant", content="이전 답변")]
    run_chat(_CONN, message="강남 아파트", history=hist, context_spec=None,
             runner=runner, search_fn=_search_returns(cands), reputation_fn=_no_rep)
    synth = next(p for p in runner.prompts if "안내 에이전트" in p)
    assert "지어내지 말" in synth and "출처" in synth and "미수집" in synth  # ground-only/cite/정직
    assert "A1" in synth and "역삼래미안" in synth  # 수집 데이터
    assert "이전 질문" in synth and "이전 답변" in synth  # 멀티턴 history 스레딩


# ── (d) referenced_complexes 실id만 — 답변이 비-후보 id 언급해도 drop ──
def test_referenced_complexes_real_ids_only() -> None:
    cands = [_cand("A1", "역삼래미안", price=142000)]
    # 답변이 후보에 없는 'FAKE99'와 후보 'A1'을 둘 다 언급 → A1만 반환(날조 drop).
    runner = MockRunner({"hard": {"property_type": "apartment"}},
                        "A1(역삼래미안)과 FAKE99를 추천합니다.")
    r = run_chat(_CONN, message="강남 아파트", history=[], context_spec=None,
                 runner=runner, search_fn=_search_returns(cands), reputation_fn=_no_rep)
    assert r.referenced_complexes == ["A1"]  # FAKE99 drop(수집 후보 실 id만)


# ── 무후보 → 정직 무결과(날조 0) ──
def test_no_candidates_honest_empty() -> None:
    runner = MockRunner({"hard": {"property_type": "apartment"}}, "맞는 단지가 없습니다.")
    r = run_chat(_CONN, message="강남 아파트", history=[], context_spec=None,
                 runner=runner, search_fn=_search_returns([]), reputation_fn=_no_rep)
    assert r.referenced_complexes == [] and r.citations == []
    synth = next(p for p in runner.prompts if "안내 에이전트" in p)
    assert "무결과" in synth  # 수집 후보 없음 → 정직


# ── 평판 RAG grounding(열린 질문) + 인용 ──
def test_open_question_grounds_reputation() -> None:
    cands = [_cand("A1", "역삼래미안", price=142000)]
    runner = MockRunner({"reputation_query": "조용한"}, "역삼래미안은 조용하다는 평이 있습니다.")
    def rep_fn(_c, cid, _q):  # type: ignore[no-untyped-def]
        return ReputationResult(
            status=REP_READY, summary="조용하다는 평",
            citations=[Citation("blog", "https://blog/rev", "p0", "조용함")])
    ctx = HardFilterSpec.model_validate(
        {"min_lat": 37.4, "max_lat": 37.6, "min_lng": 127.0, "max_lng": 127.1})
    r = run_chat(_CONN, message="이 동네 조용해?", history=[], context_spec=ctx,
                 runner=runner, search_fn=_search_returns(cands), reputation_fn=rep_fn)
    synth = next(p for p in runner.prompts if "안내 에이전트" in p)
    assert "평판요약" in synth and "조용하다는 평" in synth  # RAG grounding 주입
    assert any(c.source_url == "https://blog/rev" and c.span_ref == "p0" for c in r.citations)


# ── graceful: 평판 엔진 down(None) → 구조화 데이터로 답변(crash 0) ──
def test_graceful_reputation_down_structured_answer() -> None:
    # reputation_fn=None(평판 엔진 미가용·엔드포인트 try/except가 None화) → 평판 grounding 생략,
    # 구조화 후보 데이터로 답변·crash 0. (실제 throw→None화는 엔드포인트 래퍼 책임·아래서 검증.)
    cands = [_cand("A1", "역삼래미안", price=142000)]
    runner = MockRunner({"reputation_query": "조용한"}, "역삼래미안 정보입니다.")
    ctx = HardFilterSpec.model_validate(
        {"min_lat": 37.4, "max_lat": 37.6, "min_lng": 127.0, "max_lng": 127.1})
    r = run_chat(_CONN, message="강남 아파트 조용한", history=[], context_spec=ctx,
                 runner=runner, search_fn=_search_returns(cands), reputation_fn=_no_rep)
    assert r.answer == "역삼래미안 정보입니다." and r.referenced_complexes == ["A1"]


# ── 엔드포인트 계약(키리스): 시드 DB + mock 러너 → 200 + shape. 평판은 embed down→graceful None. ──
def test_chat_endpoint_contract(search_db) -> None:  # type: ignore[no-untyped-def]
    from collections.abc import Iterator

    from fastapi.testclient import TestClient

    from app.main import app, get_db, get_query_runner

    runner = MockRunner({"hard": {"property_type": "apartment"}}, "역삼자이(C1)를 추천합니다.")

    def _db() -> Iterator[sqlite3.Connection]:
        yield search_db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_query_runner] = lambda: runner
    try:
        client = TestClient(app)
        resp = client.post("/chat", json={
            "message": "강남 아파트",
            "history": [{"role": "user", "content": "안녕"}],
            "context": {"min_lat": 37.4, "max_lat": 37.6, "min_lng": 127.0, "max_lng": 127.1},
        })
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) >= {"answer", "referenced_complexes", "citations", "updated_spec"}
        assert body["answer"] == "역삼자이(C1)를 추천합니다."
        assert "C1" in body["referenced_complexes"]  # 시드 실 id(역삼자이 언급)
        assert body["updated_spec"] is not None  # 필터 메시지 → spec 갱신
        # 평판 엔진(embed/gemma)은 키리스서 down → graceful(crash 0·답변은 구조화로 정상)
    finally:
        app.dependency_overrides.clear()
