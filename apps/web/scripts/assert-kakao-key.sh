#!/usr/bin/env bash
# kakao-key-build-durable: post-build 키 단언 — 프로덕션 번들에 non-empty Kakao JS 키가
# 인라인됐는지 검증. 키리스(Makefile 빈 export 상속) 빌드가 prod로 조용히 배포되는 걸 차단.
#   빈-키(키리스) 빌드 → loud FAIL · 실 키 인라인 빌드 → PASS.
# 인자(테스트용·기본은 실 경로): $1=.env 파일 · $2=.next/static 디렉토리.
set -euo pipefail

WEB_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${1:-$WEB_DIR/.env.local}"
NEXT_DIR="${2:-$WEB_DIR/.next/static}"

# 키 값은 출력하지 않는다(시크릿 위생 — public 클라 키여도 규율 유지).
KEY="$(grep -E '^NEXT_PUBLIC_KAKAO_JS_KEY=' "$ENV_FILE" 2>/dev/null | head -1 \
  | cut -d= -f2- | tr -d "\"' " || true)"

if [ -z "$KEY" ]; then
  echo "✗ FAIL: $ENV_FILE 에 NEXT_PUBLIC_KAKAO_JS_KEY 없음/빈값 — 프로덕션은 실 키 필요" >&2
  exit 1
fi
if [ ! -d "$NEXT_DIR" ]; then
  echo "✗ FAIL: 빌드 산출물 없음($NEXT_DIR) — 먼저 'make build-web-prod'" >&2
  exit 1
fi

# grep no-match(exit 1)이 pipefail+set -e로 스크립트를 조기종료하지 않게 || true (n="0" 유지).
n="$(grep -rlF "$KEY" "$NEXT_DIR" 2>/dev/null | wc -l | tr -d ' ')" || true
if [ "${n:-0}" -gt 0 ]; then
  echo "✓ PASS: 프로덕션 번들에 Kakao 키 인라인됨($n 파일) — 실지도 서빙"
  exit 0
fi

echo "✗ FAIL: 번들에 키 없음 = 빈-키(키리스) 빌드(지도 폴백). 원인: Makefile 빈 export 상속." >&2
echo "        복구: cd apps/web && unset NEXT_PUBLIC_KAKAO_JS_KEY && npm run build (= make build-web-prod)" >&2
exit 1
