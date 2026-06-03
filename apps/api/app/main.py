"""ht-estate API — Phase 0.

헬스 슬라이스 + hard filter 검색(T0-6) + NL 검색(P4-2b). 라우트는 얇게: 검증→쿼리→직렬화.
NL 경로는 parse_query(claude -p)로 NL→spec 후 동일 hard 필터+랭킹 재사용(수동 spec 경로 유지).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.search.floorplan import attach_floorplan
from app.search.gym import attach_gym
from app.search.nl_parse import (
    ClaudeRunner,
    Detected,
    QueryParseError,
    _default_runner,
    parse_query,
)
from app.search.pet import attach_pet
from app.search.ranking import rank_candidates
from app.search.repo import Candidate, search_complexes
from app.search.review import attach_review
from app.search.spec import HardFilterSpec
from app.store.db import get_connection

app = FastAPI(title="ht-estate API", version="0.1.0")

# dev 프론트(Next.js localhost:3000)가 API를 호출할 수 있게 — 개인 단계 범위.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db() -> Iterator[sqlite3.Connection]:
    """요청당 DB 커넥션. 테스트는 dependency_overrides로 :memory: 시드 커넥션 주입."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def get_query_runner() -> ClaudeRunner:
    """NL 파서의 claude -p 러너. 테스트는 dependency_overrides로 mock 주입(키리스 게이트)."""
    return _default_runner


class NlQuery(BaseModel):
    """NL 검색 요청 — 자유 텍스트 질의."""

    query: str


class NlSearchResponse(BaseModel):
    """NL 검색 응답 — 확정 spec(투명성) + 감지·반영 + 매핑 불가 + 후보."""

    spec: HardFilterSpec
    detected: list[Detected]
    unsupported: list[str]
    candidates: list[Candidate]


def _run_search(conn: sqlite3.Connection, spec: HardFilterSpec) -> list[Candidate]:
    """hard filter → soft 부착 → 가중합 랭킹(demote-not-exclude). 수동/NL 경로 공용.

    하드만 SET 결정. attach_* 후 활성 soft 조건(gym/pet enrichment + 구조화) 가중합으로 ORDER만
    재정렬(SET 불변)하고 조건별 평가(criteria_eval)를 후보에 표면화(§7). soft 비활성이면 중립 정렬.
    review/floorplan은 레지스트리 밖이라 랭킹 신호 아님(표시 전용). read-through라 query-time 읽기.
    """
    candidates = search_complexes(conn, spec)
    now = datetime.now(UTC)
    attach_gym(conn, candidates, now=now)  # soft 조건(gym) 사실 부착
    attach_pet(conn, candidates, now=now)  # soft 조건(pet) 사실 부착
    attach_review(conn, candidates, now=now)  # 표시 전용 — 레지스트리 밖(랭킹 신호 아님, P3-1)
    attach_floorplan(conn, candidates, now=now)  # 표시 전용 — 레지스트리 밖(랭킹 아님, P3-2)
    return rank_candidates(candidates, spec.soft)


@app.get("/health")
def health() -> dict[str, str]:
    """헬스 체크 — 게이트/스모크용 결정론 엔드포인트."""
    return {"status": "ok"}


@app.post("/complexes/search")
def search_complexes_endpoint(
    spec: HardFilterSpec, conn: Annotated[sqlite3.Connection, Depends(get_db)]
) -> list[Candidate]:
    """구조화 hard filter_spec → 후보(이진 in/out) + soft 조건 부착·랭킹(P4-2a). 수동 경로."""
    return _run_search(conn, spec)


@app.post("/complexes/search/nl")
def search_complexes_nl_endpoint(
    body: NlQuery,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    runner: Annotated[ClaudeRunner, Depends(get_query_runner)],
) -> NlSearchResponse:
    """자연어 질의 → parse_query(레지스트리 grounding) → 동일 hard 필터+랭킹 (P4-2b).

    NL을 #2a 레지스트리 조건에 매핑(hard/soft 분류·모호→soft). 감지·반영(detected)과 매핑 불가
    구절(unsupported)을 함께 표면화(#3 칩·튜닝 재료). 파싱 불가(빈 응답·JSON 아님·모순) → 422.
    runner는 dependency라 테스트가 mock으로 주입(게이트 키리스). 확정 spec도 응답에 실어 투명성.
    """
    try:
        parsed = parse_query(body.query, runner=runner)
    except QueryParseError as exc:
        raise HTTPException(status_code=422, detail=f"질의를 spec으로 파싱 실패: {exc}") from exc
    candidates = _run_search(conn, parsed.spec)
    return NlSearchResponse(
        spec=parsed.spec,
        detected=parsed.detected,
        unsupported=parsed.unsupported,
        candidates=candidates,
    )
