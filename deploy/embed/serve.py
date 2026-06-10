"""ht-estate embed/rerank 서비스 (E3-1) — bge-m3 embed + bge-reranker-v2-m3 rerank, OpenAI-호환.

ARM64 GB10(sm_121)서 prebuilt 임베딩 컨테이너(Infinity/TEI = amd64)가 안 도므로 **네이티브 Python
서빙**. CPU-first(lazy 볼륨 충분·멀티테넌트 GPU 무경합 — gemma :8087 옆). localhost:8092 핀.

  POST /v1/embeddings  {model?, input:[texts]}      → {model, data:[{index, embedding:[1024]}]}
  POST /v1/rerank      {query, documents:[texts], top_n?} → {results:[{index, score}](desc)}
  GET  /health

전용 venv(deploy/embed/.venv: sentence-transformers + torch(ARM64 CPU 휠) + fastapi + uvicorn).
모델은 startup(lifespan)서 1회 로드. GPU는 문서화된 미래 최적화(EMBED_DEVICE=cuda·박스 torch/sm_121).

    uv venv deploy/embed/.venv && uv pip install --python deploy/embed/.venv \
        sentence-transformers fastapi 'uvicorn[standard]'
    deploy/embed/.venv/bin/uvicorn serve:app --host 127.0.0.1 --port 8092   # (cwd=deploy/embed)
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from pydantic import BaseModel

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder, SentenceTransformer

EMBED_MODEL = os.environ.get("EMBED_SERVE_MODEL", "BAAI/bge-m3")
RERANK_MODEL = os.environ.get("RERANK_SERVE_MODEL", "BAAI/bge-reranker-v2-m3")
DEVICE = os.environ.get("EMBED_DEVICE", "cpu")  # 미래: 'cuda'(박스 torch·sm_121)

_embed: SentenceTransformer | None = None
_rerank: CrossEncoder | None = None


def _embedder() -> SentenceTransformer:
    global _embed
    if _embed is None:
        from sentence_transformers import SentenceTransformer
        _embed = SentenceTransformer(EMBED_MODEL, device=DEVICE)
    return _embed


def _reranker() -> CrossEncoder:
    global _rerank
    if _rerank is None:
        from sentence_transformers import CrossEncoder
        _rerank = CrossEncoder(RERANK_MODEL, device=DEVICE)
    return _rerank


@asynccontextmanager
async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
    _embedder()  # startup 1회 로드(ready 후 active — 느린 첫호출 방지)
    _reranker()
    yield


app = FastAPI(title="ht-estate embed/rerank", version="0.1.0", lifespan=lifespan)


class EmbeddingsRequest(BaseModel):
    input: list[str]
    model: str | None = None


class RerankRequest(BaseModel):
    query: str
    documents: list[str]
    top_n: int | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/embeddings")
def embeddings(req: EmbeddingsRequest) -> dict:
    vecs = _embedder().encode(req.input, normalize_embeddings=True)
    return {
        "model": EMBED_MODEL,
        "data": [
            {"object": "embedding", "index": i, "embedding": v.tolist()}
            for i, v in enumerate(vecs)
        ],
    }


@app.post("/v1/rerank")
def rerank(req: RerankRequest) -> dict:
    scores = _reranker().predict([(req.query, d) for d in req.documents])
    results = sorted(
        ({"index": i, "score": float(s)} for i, s in enumerate(scores)),
        key=lambda r: r["score"],
        reverse=True,
    )
    if req.top_n is not None:
        results = results[: req.top_n]
    return {"results": results}
