"""ht-estate API — Phase 0.

헬스 슬라이스 + hard filter 검색(T0-6) + NL 검색(P4-2b). 라우트는 얇게: 검증→쿼리→직렬화.
NL 경로는 parse_query(claude -p)로 NL→spec 후 동일 hard 필터+랭킹 재사용(수동 spec 경로 유지).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import app.settings  # noqa: F401  (루트 .env 로딩 — provider/fetcher env 활성화: 온디맨드 라이브)
from app.corpus.ondemand import OnDemandCorpus
from app.corpus.vec import ensure_vec_table
from app.embed.client import EmbedClient, embed_client_from_env
from app.enrich.extractors.gym_verify import GYM_VERIFIED
from app.enrich.fetcher import NullFetcher, naver_fetcher_from_env
from app.enrich.ondemand import READY, OnDemandEnricher
from app.enrich.provider import LLMProvider, provider_from_env
from app.enrich.store import read_facts
from app.poi.store import attach_poi
from app.reputation.service import PENDING as REP_PENDING
from app.reputation.service import UNAVAILABLE as REP_UNAVAILABLE
from app.reputation.service import synthesize_reputation
from app.school.assignment import attach_assignment
from app.school.store import attach_school
from app.search.cache import cached
from app.search.criteria import criteria_catalog, quick_filters_catalog
from app.search.floorplan import attach_floorplan
from app.search.gym import GymSummary, attach_gym, synthesize_gym
from app.search.nl_parse import (
    ClaudeRunner,
    Detected,
    QueryParseError,
    _default_runner,
    parse_query,
)
from app.search.pet import ALIAS_ATTRIBUTES, PetSummary, attach_pet, synthesize_pet
from app.search.ranking import rank_candidates
from app.search.repo import (
    Candidate,
    MarkerFeed,
    UnitTypeCatalog,
    search_complexes,
    search_marker_feed,
    unit_type_catalog,
)
from app.search.review import attach_review
from app.search.spec import HardFilterSpec
from app.store.db import get_connection
from app.store.pipeline_state import read_pipeline_state

logger = logging.getLogger("ht-estate")

# instant-perf: 프론트 mount의 DEFAULT_BBOX(page.tsx)와 일치 — startup 워밍이 첫 유저 로드를 웜으로.
_WARM_BBOX = {"min_lat": 37.4, "max_lat": 37.7, "min_lng": 126.8, "max_lng": 127.2}
_WARM_MOUNT_LEVEL = 5  # MapView 초기 level — mount 시 /markers가 보내는 줌(동 레벨 클러스터)


def _warm() -> None:
    """startup 1회 — DEFAULT_BBOX의 /search·/markers를 선실행해 응답 캐시 + SQLite 페이지캐시 워밍.

    첫 유저 로드가 콜드(~0.5s)가 아니라 캐시 히트(~즉시)가 된다. **read-only**(쓰기 0)라 신선도/
    지문/counts 불변. 워밍은 캐시 래퍼를 그대로 호출 → 유저 경로와 동일 키로 적재(=첫 요청이 히트).
    """
    conn = get_connection()
    try:
        search_spec = HardFilterSpec.model_validate({**_WARM_BBOX, "limit": 100})
        _run_search_cached(conn, search_spec)
        marker_spec = HardFilterSpec.model_validate(
            {**_WARM_BBOX, "limit": 100, "level": _WARM_MOUNT_LEVEL}
        )
        _markers_cached(conn, marker_spec)
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """startup 워밍(graceful — 실패해도 부팅 막지 않음). shutdown 정리 없음."""
    try:
        _warm()
    except Exception as exc:  # noqa: BLE001 — 워밍 실패는 비치명(첫 요청이 콜드일 뿐)
        logger.warning("startup warm skipped: %s", exc)
    yield


app = FastAPI(title="ht-estate API", version="0.1.0", lifespan=lifespan)

# dev 프론트(Next.js localhost:3000)가 API를 호출할 수 있게 — 개인 단계 범위.
# 배포(LAN/터널) 시 `CORS_ORIGINS`(콤마목록·`*`)로 허용 출처 확장. 미설정 시 기존 로컬만(불변).
_CORS_ORIGINS_ENV = os.environ.get("CORS_ORIGINS", "").strip()
_cors_origins = (
    [o.strip() for o in _CORS_ORIGINS_ENV.split(",") if o.strip()]
    if _CORS_ORIGINS_ENV
    else ["http://localhost:3000", "http://127.0.0.1:3000"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
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


# 온디맨드 추출기 싱글톤 — inflight 디덥/음성 쿨다운 상태를 요청 간 공유해야 하므로 모듈 1회 구성.
# provider/fetcher는 env(.env). 미구성이면 provider=None → 엔드포인트 unavailable(검색·게이트 불변).
_default_enricher = OnDemandEnricher(
    provider=provider_from_env(),
    fetcher=naver_fetcher_from_env() or NullFetcher(),
)


def get_enricher() -> OnDemandEnricher:
    """온디맨드 추출기. 테스트는 dependency_overrides로 mock 주입(키리스)."""
    return _default_enricher


# 평판(E3-3) 의존 — 코퍼스 트리거(OnDemandCorpus·E3-2) + embed/rerank(:8092) + gemma(ENRICH_LLM).
# embed_client 1개가 embed+rerank 둘 다(EmbedClient는 Embedder·Reranker 구현). provider 미구성이면
# synth는 인용만(evidence-only). fetcher 미구성이면 코퍼스 unavailable. API 트리거 build는 gym/pet
# 동형 lockless(graceful: DB 락 경합 시 defer) — 배치 C47은 build_corpus.py CLI.
@dataclass
class ReputationDeps:
    corpus: OnDemandCorpus
    embed_client: EmbedClient  # embed + rerank 겸용
    provider: LLMProvider | None


_reputation_embed = embed_client_from_env()
_default_reputation_deps = ReputationDeps(
    corpus=OnDemandCorpus(
        fetcher=naver_fetcher_from_env(), embed_client=_reputation_embed,
        provider=provider_from_env(),  # 적재시 gemma 분류기(bulk와 동형 정밀·개발기사/타지 reject)
    ),
    embed_client=_reputation_embed,
    provider=provider_from_env(),
)


def get_reputation() -> ReputationDeps:
    """평판 의존 번들. 테스트는 dependency_overrides로 mock 주입(키리스)."""
    return _default_reputation_deps


class NlQuery(BaseModel):
    """NL 검색 요청 — 자유 텍스트 질의."""

    query: str


class NlSearchResponse(BaseModel):
    """NL 검색 응답 — 확정 spec(투명성) + 감지·반영 + 매핑 불가 + 후보 + 평판 의도.

    reputation_query: 주관적 평판 의도 free-text(없으면 None) → 프론트가 detail 평판 섹션(E3 RAG)에
    pre-seed. **검색/랭킹 경로서 평판 synth 0**(detail 열 때만 lazy — 50후보 인라인 금지).
    """

    spec: HardFilterSpec
    detected: list[Detected]
    unsupported: list[str]
    candidates: list[Candidate]
    reputation_query: str | None = None


class GymSection(BaseModel):
    """온디맨드 gym 섹션 — status(ready/pending/unavailable) + 합성(ready일 때만)."""

    status: str
    summary: GymSummary | None


class PetSection(BaseModel):
    """온디맨드 pet 섹션 — status + 합성(advisory: confirm/caveats는 summary에 보존)."""

    status: str
    summary: PetSummary | None


class EnrichmentResponse(BaseModel):
    """단지 상세용 온디맨드 enrichment — gym/pet 섹션별 status + 캐시 합성."""

    complex_id: str
    gym: GymSection
    pet: PetSection


class ReputationQuery(BaseModel):
    """평판 질의 — 열린 텍스트(소음·주차·관리·교통·층간소음 등)."""

    query: str


class CitationOut(BaseModel):
    """인용 1건 — 딥링크 정밀(source_url + span_ref) + 근거 발췌."""

    source_type: str
    source_url: str
    span_ref: str | None
    snippet: str


class ReputationResponse(BaseModel):
    """평판 응답 — status + 종합(degrade 시 None) + 인용 + degraded(투명성).

    advisory: 후기는 주관적·확인 권장(단정 아님) — 프론트가 배지 표기. summary None이면
    인용만(evidence-only·gemma degrade) 또는 매치 0. status=pending이면 코퍼스 수집 중.
    """

    complex_id: str
    status: str
    summary: str | None
    citations: list[CitationOut]
    degraded: list[str]


def _run_search(conn: sqlite3.Connection, spec: HardFilterSpec) -> list[Candidate]:
    """hard filter → soft 부착 → 가중합 랭킹(demote-not-exclude). 수동/NL 경로 공용.

    하드만 SET 결정. attach_* 후 활성 soft 조건(gym/pet enrichment + 구조화) 가중합으로 ORDER만
    재정렬(SET 불변)하고 조건별 평가(criteria_eval)를 후보에 표면화(§7). soft 비활성이면 중립 정렬.
    review/floorplan은 레지스트리 밖이라 랭킹 신호 아님(표시 전용). read-through라 query-time 읽기.
    """
    candidates = search_complexes(conn, spec)
    now = datetime.now(UTC)
    attach_poi(conn, candidates)  # 정적 POI 근접(eager Tier-1) 부착 — 카드 표시
    attach_school(conn, candidates)  # 학교 거리 근접(eager Tier-1) 부착 — 카드 표시(school-1)
    attach_assignment(conn, candidates)  # 배정 초등 통학구역(advisory) 부착 — 카드 표시(school-2)
    attach_gym(conn, candidates, now=now)  # soft 조건(gym) 사실 부착
    attach_pet(conn, candidates, now=now)  # soft 조건(pet) 사실 부착
    attach_review(conn, candidates, now=now)  # 표시 전용 — 레지스트리 밖(랭킹 신호 아님, P3-1)
    attach_floorplan(conn, candidates, now=now)  # 표시 전용 — 레지스트리 밖(랭킹 아님, P3-2)
    return rank_candidates(candidates, spec.soft)


# instant-perf: 응답 캐시 래퍼 — 무거운 광역 재계산을 메모이즈(데이터 시그니처로 무효화·stale 0).
# level은 /search 결과에 무관(클러스터 전용)이라 검색 키서 제외 → 줌만 바뀐 동일 bbox는 히트.
# limit은 마커 결과에 무관(전부 반환/집계)이라 마커 키서 제외. 결과는 신선 계산과 동일(속도만).
def _run_search_cached(conn: sqlite3.Connection, spec: HardFilterSpec) -> list[Candidate]:
    return cached(
        "search", conn, spec.model_dump_json(exclude={"level"}),
        lambda: _run_search(conn, spec),
    )


def _markers_cached(conn: sqlite3.Connection, spec: HardFilterSpec) -> MarkerFeed:
    return cached(
        "markers", conn, spec.model_dump_json(exclude={"limit"}),
        lambda: search_marker_feed(conn, spec),
    )


@app.get("/health")
def health() -> dict[str, str]:
    """헬스 체크 — 게이트/스모크용 결정론 엔드포인트."""
    return {"status": "ok"}


@app.get("/criteria")
def criteria_endpoint() -> dict[str, list[dict[str, object]]]:
    """조건 카탈로그(read-only) — criteria.REGISTRY + 퀵필터 직렬화. 프론트 필터 UI registry-driven.

    인메모리 REGISTRY/QUICK_FILTERS 직렬화 → **DB 무접촉**(지문/counts 불변). 신규 criteria 등록 시
    프론트 토글/뱃지 자동 동기(하드코딩 드리프트 0·search-deepen-1 철학 연장)."""
    return {"criteria": criteria_catalog(), "quick_filters": quick_filters_catalog()}


@app.get("/pipeline-state")
def pipeline_state_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, list[dict[str, object]]]:
    """적재 파이프라인 자기서술 원장(read-only·pipeline-state) — 출생/목표/진행/마지막실행/상태.

    "얼마나 됐지·정상인지·언제 시작" = 한 호출 자기서술(git·메모리 불요). pipeline_state META만
    SELECT → canonical 무접촉(지문/counts 불변). introduced_at=출생(provenance)·metric=세는 대상."""
    return {"pipelines": read_pipeline_state(conn)}


@app.post("/complexes/search")
def search_complexes_endpoint(
    spec: HardFilterSpec, conn: Annotated[sqlite3.Connection, Depends(get_db)]
) -> list[Candidate]:
    """구조화 hard filter_spec → 후보(이진 in/out) + soft 조건 부착·랭킹(P4-2a). 수동 경로."""
    return _run_search_cached(conn, spec)


@app.post("/complexes/markers")
def markers_endpoint(
    spec: HardFilterSpec, conn: Annotated[sqlite3.Connection, Depends(get_db)]
) -> MarkerFeed:
    """지도 마커 피드 — 서버가 밀도로 모드 결정(server-marker-clustering).

    매칭 ≤MAX → mode='markers'(개별 전부·절단 0) · 초과 → mode='clusters'(grid 집계·무편향·완전).
    편향 `ORDER BY complex_id LIMIT` 제거(부천-굶김 픽스) — 전 구역 표현. 동일 hard 필터 재사용·
    랭킹/soft 없음(리스트가 /search로 랭킹 담당). read-only(COUNT+GROUP BY) → 지문/counts 불변.
    instant-perf: 응답 캐시(데이터 시그니처 무효화) — 광역 ppp 집계를 웜 히트로(맵 first-paint).
    """
    return _markers_cached(conn, spec)


@app.get("/complexes/{complex_id}/unit-types")
def unit_types_endpoint(
    complex_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    deal_type: str = "sale",
) -> UnitTypeCatalog:
    """전 세대타입(unit-type-catalog) — unit_type catalog ∪ 실거래 병합(거래+미거래·세대수).

    has_catalog면 전 타입(미거래 포함)·아니면 거래된 평형만(graceful 폴백·현 거동). deal_type별
    실거래 매칭. unit_type·txn read만(좌표/canonical 무접촉 → 지문/counts 불변)."""
    spec = HardFilterSpec.model_validate({"deal_type": deal_type})
    return unit_type_catalog(conn, spec, complex_id)


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
    candidates = _run_search_cached(conn, parsed.spec)  # 평판 synth 없음 — detail-트리거 별도
    return NlSearchResponse(
        spec=parsed.spec,
        detected=parsed.detected,
        unsupported=parsed.unsupported,
        candidates=candidates,
        reputation_query=parsed.reputation_query,  # 주관 평판 의도 → 프론트 detail pre-seed
    )


@app.get("/complexes/{complex_id}/enrichment")
def complex_enrichment_endpoint(
    complex_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    enricher: Annotated[OnDemandEnricher, Depends(get_enricher)],
) -> EnrichmentResponse:
    """단지 상세 온디맨드 gym/pet (ux-1) — 캐시 즉답·miss는 백그라운드 추출+pending.

    **검색·마커와 별개**(`_run_search` 무접촉). 카드가 22–60s 블록하지 않도록 miss는 즉시 pending
    반환하고 단건만 백그라운드 추출(디덥·후보한정·graceful). pet은 레거시 `pet_allowed` 별칭 폴백.
    enrichment 테이블만 write → 지문·건물/거래 수 불변.
    """
    row = conn.execute(
        "SELECT 1 FROM complex WHERE complex_id = ? LIMIT 1", (complex_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="단지를 찾을 수 없습니다")
    now = datetime.now(UTC)
    # gym(gym-evidence): 빠른 Kakao 위치('gym') 즉답 + doc 교차검증('gym_verified') 온디맨드 → 결합.
    # Kakao 사실은 read만(배치 적재)·doc 검증은 status가 트리거(핫패스 밖). 둘 다 있으면 결합 표시.
    gym_facts = read_facts(conn, complex_id, "gym", now=now)  # kakao_local(+레거시)
    ver_state, ver_facts = enricher.status(conn, complex_id, GYM_VERIFIED, now=now)  # doc 검증
    combined_gym = gym_facts + ver_facts
    gym_state = READY if combined_gym else ver_state  # Kakao 있으면 즉답·없으면 doc 검증 상태
    pet_state, pet_facts = enricher.status(
        conn, complex_id, "pet", alias=ALIAS_ATTRIBUTES, now=now
    )
    return EnrichmentResponse(
        complex_id=complex_id,
        gym=GymSection(
            status=gym_state,
            summary=synthesize_gym(combined_gym) if gym_state == READY else None,
        ),
        pet=PetSection(
            status=pet_state,
            summary=synthesize_pet(pet_facts) if pet_state == READY else None,
        ),
    )


@app.post("/complexes/{complex_id}/reputation")
def complex_reputation_endpoint(
    complex_id: str,
    body: ReputationQuery,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    deps: Annotated[ReputationDeps, Depends(get_reputation)],
) -> ReputationResponse:
    """단지 평판 RAG (E3-3) — 열린 질의 → 코퍼스(E3-2)서 retrieve+rerank+gemma 종합+인용.

    **검색 패스 아님**(detail 트리거·느림). 코퍼스 miss/만료 → OnDemandCorpus build 트리거 +
    **pending**(동기 차단 0). 신선 → 질의 embed→단지필터 KNN→rerank(:8092)→gemma 종합(요약+인용·
    단정 금지·DB권). 3 모델 각각 graceful degrade(embed→pending·rerank→KNN fallback·gemma→인용만)·
    crash 0. **read-only**(canon write 0 → 지문/counts 불변. 트리거 build는 review_chunk/_vec만).
    """
    row = conn.execute(
        "SELECT name FROM complex WHERE complex_id = ? LIMIT 1", (complex_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="단지를 찾을 수 없습니다")
    name = row["name"] or complex_id
    now = datetime.now(UTC)
    state = deps.corpus.ensure(conn, complex_id, name, now=now)  # miss→build 트리거+pending
    if state in (REP_PENDING, REP_UNAVAILABLE):
        return ReputationResponse(
            complex_id=complex_id, status=state, summary=None, citations=[], degraded=[]
        )
    ensure_vec_table(conn)  # 읽기 conn에 vec 로드(+구스키마 마이그레이트)
    result = synthesize_reputation(
        conn, complex_id, body.query,
        embed_client=deps.embed_client, rerank_client=deps.embed_client, provider=deps.provider,
    )
    return ReputationResponse(
        complex_id=complex_id,
        status=result.status,
        summary=result.summary,
        citations=[
            CitationOut(
                source_type=c.source_type, source_url=c.source_url,
                span_ref=c.span_ref, snippet=c.snippet,
            )
            for c in result.citations
        ],
        degraded=result.degraded,
    )
