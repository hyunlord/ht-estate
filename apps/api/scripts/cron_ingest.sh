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
# coexist-1: 락 대기 상한(초). 거래 cron이 동시 enrich의 배치-해제창을 *기다려* 확실히 잡는다.
# 15분 tick 간격보다 짧게(기본 300s) → pile-up 방지. enrich 미가동 시 첫 시도 즉시 획득(무대기).
WAIT_SEC="${5:-300}"

cd "$APP_DIR"

# 공유 락 acquire — 바운드 대기-재시도(coexist-1). stale 가드 포함(매 재시도). source 필수($$ 보존).
. "$APP_DIR/scripts/lib_lock.sh"
if ! acquire_shared_lock "$LOCK" "$TTL_MIN" "$WAIT_SEC" 3 "$LOG"; then
  echo "$(date '+%F %T') [skip ] $STAGES — 락 대기 ${WAIT_SEC}s 초과(다른 적재 장기 점유)" >> "$LOG"
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
