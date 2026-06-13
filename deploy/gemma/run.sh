#!/usr/bin/env bash
# ht-estate 전용 Gemma(QAT GGUF) llama.cpp 컨테이너 — 멀티테넌트 kill 방지 핀.
# GPU 검증된 호스트 네이티브 빌드(GB10 sm_121)를 bind-mount로 재사용. localhost:8087 전용.
# --reasoning off: Gemma thinking 비활성(직접 JSON을 message.content로 — provider.py가 읽음).
set -euo pipefail

NAME=ht-gemma
PORT=8087
# GPU 레이어 오프로드(GEMMA_NGL) — 기본 99=전 레이어 GPU(현 프로덕션·머지 no-op). GPU 풋프린트
# 감축이 필요하면 재기 시 env로 낮춤: GEMMA_NGL=0 ./run.sh(CPU-only·GPU ~0) 또는 부분값(예 20).
# 저볼륨 온디맨드(gym/pet/RAG)+야간 코퍼스라 CPU 지연 수용 가능하면 0이 GPU를 통째로 반납.
GEMMA_NGL=${GEMMA_NGL:-99}
MODEL=/home/hyunlord/models/poc/gemma-4-12B-it-qat-UD-Q4_K_XL.gguf
LLAMA=/home/hyunlord/repos/llama.cpp/build
CUDA=/usr/local/cuda

docker rm -f "$NAME" 2>/dev/null || true
exec docker run -d --name "$NAME" --restart unless-stopped --gpus all \
  -p 127.0.0.1:${PORT}:${PORT} \
  -v "${LLAMA}":/llama:ro \
  -v "${CUDA}":/usr/local/cuda:ro \
  -v "${MODEL}":/model.gguf:ro \
  -e LD_LIBRARY_PATH=/llama/bin:/usr/local/cuda/targets/sbsa-linux/lib \
  ht-gemma-llamacpp:1 \
  /llama/bin/llama-server -m /model.gguf --host 0.0.0.0 --port ${PORT} \
    -ngl "${GEMMA_NGL}" -c 4096 --alias gemma-qat --jinja --reasoning off
