#!/bin/sh
# 건축물대장 enrich 일일 cron 래퍼 (enrich-1d) — 비아파트 잔여 무인 멀티데이 재개.
#
# - **self-shlock**(.enrich.lock): 전일 enrich run이 살아있으면 이 tick skip(미중첩). 공유
#   .ingest.lock과 **별개** — enrich-vs-enrich 가드용.
# - **거래 cron 공존**(C47): 러너가 *내부적으로* 공유 .ingest.lock(ShlockBatch)을 배치단위로
#   잡았다 놓으며, cron_ingest.sh가 acquire_shared_lock(대기-재시도)으로 그 해제창을 served.
# - **일일 쿼터**(실측 ~1만/일): --limit로 하단 여유(기본 9000). 쿼터 도달 시 러너가 HTTP 429를
#   우아하게 중단(C48) → 익일 이 cron이 --resume(러너는 항상 ledger resume) 이어받음.
# - 멱등·resume-safe·좌표 무접촉(러너 보장). 매일 누적 → ~15일 완주.
#
# usage: cron_enrich.sh [limit] [interval] [ttl_min]
set -eu

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UV="$(command -v uv 2>/dev/null || echo /opt/homebrew/bin/uv)"
ELOCK="$APP_DIR/data/.enrich.lock"   # enrich 전용 self-lock(공유 .ingest.lock과 별개)
LOG="$APP_DIR/data/enrich_cron.log"

LIMIT="${1:-9000}"
INTERVAL="${2:-0.3}"
TTL_MIN="${3:-1440}"   # 24h — SIGKILL 등 trap 우회 stale 보조 가드(죽은 PID는 shlock 자동 탈취)

cd "$APP_DIR"

# stale self-lock 가드(TTL 초과분만 — 살아있는 run 락은 보존)
if [ -f "$ELOCK" ] && [ -n "$(find "$ELOCK" -mmin "+$TTL_MIN" 2>/dev/null)" ]; then
  echo "$(date '+%F %T') [stale] enrich self-lock(${TTL_MIN}m 초과) 제거" >> "$LOG"
  rm -f "$ELOCK"
fi

# self-shlock — 전일 enrich가 진행 중(살아있는 PID)이면 skip(미중첩). 죽은 PID면 자동 탈취.
if ! /usr/bin/shlock -f "$ELOCK" -p "$$"; then
  echo "$(date '+%F %T') [skip ] 이전 enrich run 진행 중 — 이번 tick 양보" >> "$LOG"
  exit 0
fi
trap 'rm -f "$ELOCK"' EXIT INT TERM

echo "$(date '+%F %T') [start] enrich --limit $LIMIT --interval $INTERVAL" >> "$LOG"
set +e
# 러너는 항상 ledger resume(--resume 플래그 없음). 공유 .ingest.lock(기본)으로 거래 cron과 공존.
"$UV" run python scripts/enrich_building_ledger.py \
  --limit "$LIMIT" --interval "$INTERVAL" --inter-batch-sleep 2 >> "$LOG" 2>&1
rc=$?
set -e
echo "$(date '+%F %T') [done ] enrich rc=$rc" >> "$LOG"
exit 0
