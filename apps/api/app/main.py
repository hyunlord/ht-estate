"""ht-estate API — Phase 0.

헬스 슬라이스 + hard filter 검색(T0-6). 라우트는 얇게: 검증(Pydantic)→쿼리→직렬화.
NL→filter_spec(LLM)·지도 UI는 별도 티켓(L3·T0-7).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.search.gym import attach_gym
from app.search.pet import attach_pet
from app.search.repo import Candidate, search_complexes
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


@app.get("/health")
def health() -> dict[str, str]:
    """헬스 체크 — 게이트/스모크용 결정론 엔드포인트."""
    return {"status": "ok"}


@app.post("/complexes/search")
def search_complexes_endpoint(
    spec: HardFilterSpec, conn: Annotated[sqlite3.Connection, Depends(get_db)]
) -> list[Candidate]:
    """구조화 hard filter_spec → 후보 단지(이진 in/out) + Tier-2 gym·pet 부착(읽기 전용).

    soft 속성은 hard filter 후 attach_*로 부착(R1: 필터 아님). enrich(stub) read-through라
    query-time은 읽기만 — 시드 hit=사실, miss=none. 키 불필요.
    """
    candidates = search_complexes(conn, spec)
    now = datetime.now(UTC)
    attach_gym(conn, candidates, now=now)
    attach_pet(conn, candidates, now=now)
    return candidates
