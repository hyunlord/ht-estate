#!/usr/bin/env bash
# embed/rerank 서비스 전용 venv 빌드(키리스·무root) — sentence-transformers + torch(ARM64 CPU) + uvicorn.
# 모델(bge-m3·bge-reranker-v2-m3 ~4.6GB)은 서비스 첫 기동 시 HF 캐시로 다운로드. 멱등.
set -euo pipefail
cd "$(dirname "$0")"
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
echo "embed venv 빌드 완료: $(.venv/bin/python -c 'import sentence_transformers,fastapi,uvicorn;print("st",sentence_transformers.__version__)')"
