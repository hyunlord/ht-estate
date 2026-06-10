"""embed/rerank 클라이언트 (E3-1) — OpenAI-호환 embed + 우리 소유 rerank, swappable provider.

E3 RAG의 임베딩/리랭크를 **swappable config**(EMBED_BASE_URL·EMBED_MODEL·RERANK_MODEL) 뒤로
추상화한다. 기본은 Spark-native 서비스(localhost:8092)이나 API override 가능(코드 불변 — gemma
`ENRICH_LLM_*` 패턴 동형, design doc swappable 원칙).

graceful-degrade(멀티테넌트 — 모델 killable·gemma 동형): 다운·타임아웃·5xx → 짧은 백오프 재시도
후 **typed deferrable**(EmbedUnavailable/RerankUnavailable)로 올린다. 하류(E3-2/E3-3)가 catch해
새 임베딩만 defer(캐시 벡터로 검색 지속)·**crash 0**.

**레시피 핀**(임베딩 지문): embed 결과에 (embed_model, dim, normalized)를 달아 모델 변경 감지·
재임베딩 트리거(geocode 지문 규율의 임베딩판). 키리스: httpx client 주입(MockTransport).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

import httpx

BASE_URL_ENV = "EMBED_BASE_URL"
EMBED_MODEL_ENV = "EMBED_MODEL"
RERANK_MODEL_ENV = "RERANK_MODEL"

DEFAULT_BASE_URL = "http://localhost:8092/v1"  # Spark-native 서비스(:8092 — 8088은 멀티테넌트 점유)
DEFAULT_EMBED_MODEL = "bge-m3"
DEFAULT_RERANK_MODEL = "bge-reranker-v2-m3"
EMBED_DIM = 1024  # bge-m3


class EmbedUnavailable(RuntimeError):
    """embed provider 사용 불가(다운·타임아웃·5xx·malformed) — 하류 defer 신호(crash 금지)."""


class RerankUnavailable(RuntimeError):
    """rerank provider 사용 불가 — 하류 defer 신호(crash 금지)."""


@dataclass(frozen=True)
class EmbedRecipe:
    """임베딩 지문 핀 — 모델/차원/정규화. 코퍼스 write-back에 박아 모델변경 감지·재임베딩."""

    embed_model: str
    dim: int
    normalized: bool = True


@dataclass(frozen=True)
class EmbedResult:
    vectors: list[list[float]]
    recipe: EmbedRecipe


class Embedder(Protocol):
    """임베딩 쓰기 측(코퍼스 build)이 의존하는 좁은 인터페이스 — swappable·테스트 mock 가능.

    EmbedClient가 구현. 하류(corpus)는 embed + 레시피 식별(embed_model)만 필요(rerank는 E3-3)."""

    embed_model: str

    def embed(self, texts: list[str]) -> EmbedResult: ...


class Reranker(Protocol):
    """리랭크 읽기 측(E3-3 retrieval)이 의존하는 좁은 인터페이스 — swappable·테스트 mock 가능."""

    def rerank(
        self, query: str, documents: list[str], top_n: int | None = None
    ) -> list[RerankHit]: ...


@dataclass(frozen=True)
class RerankHit:
    index: int  # documents 입력 인덱스
    score: float


@dataclass
class EmbedClient:
    """OpenAI-호환 embed(`/v1/embeddings`) + 우리 소유 rerank(`/rerank`). client 주입(테스트)."""

    base_url: str = DEFAULT_BASE_URL
    embed_model: str = DEFAULT_EMBED_MODEL
    rerank_model: str = DEFAULT_RERANK_MODEL
    timeout: float = 30.0
    max_retries: int = 2
    backoff: float = 0.5
    client: httpx.Client | None = None
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)

    def _post(self, path: str, body: dict, unavailable: type[RuntimeError]) -> dict:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        own = self.client is None
        cl = self.client or httpx.Client(timeout=self.timeout)
        try:
            last: Exception | None = None
            for attempt in range(self.max_retries + 1):
                try:
                    resp = cl.post(url, json=body)
                except httpx.TransportError as exc:  # 다운·타임아웃·연결 → 일시적
                    last = exc
                else:
                    if resp.status_code < 500:
                        if resp.status_code >= 400:
                            raise unavailable(f"{path} HTTP {resp.status_code}")  # 4xx → 영구 defer
                        try:
                            return resp.json()
                        except ValueError as exc:
                            raise unavailable(f"{path} malformed JSON") from exc
                    last = httpx.HTTPStatusError(  # 5xx → 일시적(재시도)
                        f"{path} {resp.status_code}", request=resp.request, response=resp
                    )
                if attempt < self.max_retries:
                    self.sleep(self.backoff * (2**attempt))
            raise unavailable(f"{path} 재시도 {self.max_retries}회 소진: {type(last).__name__}")
        finally:
            if own:
                cl.close()

    def embed(self, texts: list[str]) -> EmbedResult:
        """텍스트 → 1024 정규화 벡터 + 레시피 핀. 실패는 EmbedUnavailable(defer)."""
        data = self._post(
            "/embeddings", {"model": self.embed_model, "input": texts}, EmbedUnavailable
        )
        try:
            vectors = [row["embedding"] for row in data["data"]]
        except (KeyError, TypeError) as exc:
            raise EmbedUnavailable("embeddings 응답 형식 오류") from exc
        return EmbedResult(
            vectors=vectors,
            recipe=EmbedRecipe(embed_model=self.embed_model, dim=EMBED_DIM, normalized=True),
        )

    def rerank(self, query: str, documents: list[str], top_n: int | None = None) -> list[RerankHit]:
        """쿼리+후보 → (index, score) 내림차순. 실패는 RerankUnavailable(defer)."""
        body: dict = {"query": query, "documents": documents}
        if top_n is not None:
            body["top_n"] = top_n
        data = self._post("/rerank", body, RerankUnavailable)
        try:
            hits = [
                RerankHit(index=int(r["index"]), score=float(r["score"])) for r in data["results"]
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise RerankUnavailable("rerank 응답 형식 오류") from exc
        return sorted(hits, key=lambda h: h.score, reverse=True)


def embed_client_from_env() -> EmbedClient:
    """env(EMBED_BASE_URL·EMBED_MODEL·RERANK_MODEL)에서 클라 구성. 미설정이면 Spark-native 기본.

    gemma `provider_from_env`와 달리 항상 클라 반환(기본=localhost:8092) — provider 미구성도
    클라 down처럼 graceful(EmbedUnavailable). swappable: API 쓰려면 env override.
    """
    return EmbedClient(
        base_url=(os.environ.get(BASE_URL_ENV, "").strip() or DEFAULT_BASE_URL),
        embed_model=(os.environ.get(EMBED_MODEL_ENV, "").strip() or DEFAULT_EMBED_MODEL),
        rerank_model=(os.environ.get(RERANK_MODEL_ENV, "").strip() or DEFAULT_RERANK_MODEL),
    )
