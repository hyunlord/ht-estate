#!/bin/sh
# ht-estate 전국 적재 cron/복구 래퍼 (OPS · fix/rent-empty-ledger #3)
#   - shlock 단일 공유 락: complex/거래/복구가 같은 락을 공유 → data.go.kr 동시 호출 0
#     (동시 호출이 burst 429 빈응답을 유발 → rent 유실의 근본 트리거였음. 직렬화로 차단).
#   - shlock는 죽은 PID 락을 자동 탈취하므로 TTL은 SIGKILL 등 trap-우회 stale 대비용 보조.
#     TTL은 인자(분)로 받고 기본을 길게(180m) → 살아있는 긴 복구런 락을 절대 탈취 안 함
#     (40m 단TTL이 46분 복구런 락을 뺏어 동시실행 사고를 낸 회귀를 방지).
#   - ingest_nationwide --resume: ledger로 완료분 skip(중복 0). --limit으로 tick 바운드.
# cron 최소환경 대비: 스크립트 위치에서 APP_DIR 도출 + uv 절대경로 폴백.
# usage: cron_ingest.sh <stages> [limit] [interval] [ttl_min]
set -eu

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UV="$(command -v uv 2>/dev/null || echo /opt/homebrew/bin/uv)"
LOCK="$APP_DIR/data/.ingest.lock"   # 단일 공유 락(직렬화)
LOG="$APP_DIR/data/ingest_cron.log"

STAGES="${1:-transaction,rent,join}"
LIMIT="${2:-8}"
INTERVAL="${3:-0.6}"
TTL_MIN="${4:-180}"

cd "$APP_DIR"

# stale 락 가드(SIGKILL 등 trap 우회분만): TTL이 충분히 길어 살아있는 작업 락은 건드리지 않음.
if [ -f "$LOCK" ] && [ -n "$(find "$LOCK" -mmin "+$TTL_MIN" 2>/dev/null)" ]; then
  echo "$(date '+%F %T') [stale] ${TTL_MIN}분 초과 락 제거" >> "$LOG"
  rm -f "$LOCK"
fi

# shlock: 살아있는 PID가 보유하면 이 tick은 조용히 skip(직렬화). 죽은 PID면 자동 탈취.
if ! /usr/bin/shlock -f "$LOCK" -p "$$"; then
  echo "$(date '+%F %T') [skip ] $STAGES — 다른 적재 실행 중" >> "$LOG"
  exit 0
fi
trap 'rm -f "$LOCK"' EXIT INT TERM

echo "$(date '+%F %T') [start] $STAGES limit=$LIMIT interval=$INTERVAL ttl=${TTL_MIN}m" >> "$LOG"
set +e
"$UV" run python scripts/ingest_nationwide.py \
  --stages "$STAGES" --resume --limit "$LIMIT" --interval "$INTERVAL" >> "$LOG" 2>&1
rc=$?
set -e
echo "$(date '+%F %T') [done ] $STAGES rc=$rc" >> "$LOG"
exit 0
