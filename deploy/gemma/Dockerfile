# ht-estate 전용 Gemma 서빙 — QAT GGUF를 llama.cpp(CUDA)로 격리 서빙.
# 멀티테넌트 kill 방지 핀(전용 컨테이너 + --restart). GPU 검증된 호스트 네이티브
# llama.cpp 빌드를 bind-mount로 재사용(GB10 sm_121 컴파일 보장) → 여기선 런타임
# 라이브러리(libgomp1)만 베이크. CUDA 13 sbsa 런타임/모델/빌드는 런타임 bind-mount.
FROM ubuntu:24.04
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 libstdc++6 ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
# 실제 실행 인자는 docker run / systemd 유닛에서 지정.
ENTRYPOINT []
