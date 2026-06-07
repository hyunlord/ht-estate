#!/bin/sh
# 동시부하 시뮬 — coexist-1 픽스 검증(ops, 키 불필요·DB 무접촉).
#
# enrich가 공유 락을 enrich-1b 패턴(배치 보유 ~82% · 배치사이 짧은 해제)으로 점유하는 동안:
#   OLD = 단발 shlock(거래 cron의 기존 거동) → 해제창을 자주 놓침(starve).
#   NEW = acquire_shared_lock(바운드 대기-재시도, lib_lock.sh) → 해제창을 *확실히* 잡음(served).
# 실제 production 함수(lib_lock.sh)를 source해 검증(드리프트 없음).
#
#   sh scripts/sim_coexist.sh
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"
. "$DIR/lib_lock.sh"

LOCK="$(mktemp -u /tmp/coexist_sim.XXXXXX.lock)"
LOG="$(mktemp /tmp/coexist_sim.XXXXXX.log)"
HOLD=9          # enrich 배치 보유(초) — enrich-1b 실측(batch_size30×interval0.3)
RELEASE=2       # 배치 사이 해제창(초) — inter-batch-sleep=2 (≈82% 점유)
CYCLES=5        # enrich 배치 수
OLD_TRIES=10    # OLD 단발 시도 횟수(한 사이클에 고르게)

cleanup() { kill "$ENRICH_PID" 2>/dev/null || true; rm -f "$LOCK" "$LOG"; }
trap cleanup EXIT INT TERM

# ── 배경: enrich 패턴 락 홀더(ShlockBatch 모사 — 스핀 획득→보유→해제→해제창) ──
(
  i=0
  while [ "$i" -lt "$CYCLES" ]; do
    # 스핀 획득(cron이 잡고 있으면 양보 후 재시도 — 상호배제 확인)
    until /usr/bin/shlock -f "$LOCK" -p "$$" 2>/dev/null; do sleep 0.3; done
    sleep "$HOLD"
    rm -f "$LOCK"        # 배치 사이 해제창
    sleep "$RELEASE"
    i=$((i + 1))
  done
) &
ENRICH_PID=$!
sleep 1   # enrich가 먼저 락을 잡게

echo "=== 동시부하 시뮬 (enrich 보유 ${HOLD}s/해제 ${RELEASE}s ≈ $((100*HOLD/(HOLD+RELEASE)))% 점유) ==="

# ── OLD: 단발 shlock 거동 측정 ──
old_ok=0; old_skip=0; n=0
while [ "$n" -lt "$OLD_TRIES" ]; do
  if /usr/bin/shlock -f "$LOCK" -p "$$" 2>/dev/null; then
    old_ok=$((old_ok + 1)); rm -f "$LOCK"   # 즉시 반납(enrich 계속하게)
  else
    old_skip=$((old_skip + 1))
  fi
  n=$((n + 1))
  sleep 1
done
echo "OLD 단발 shlock: 획득 ${old_ok}/${OLD_TRIES} · skip ${old_skip}/${OLD_TRIES}  → starve(놓친 tick)"

# ── NEW: 바운드 대기-재시도 ──
start=$(date +%s)
if acquire_shared_lock "$LOCK" 180 60 1 "$LOG"; then
  elapsed=$(( $(date +%s) - start ))
  rm -f "$LOCK"   # cron tick 종료 모사(반납 → enrich 재개)
  echo "NEW 대기-재시도: 획득 ✓  (대기 ${elapsed}s ≤ 상한 60s)  → served(starve 아님)"
  RESULT=0
else
  echo "NEW 대기-재시도: 미획득 ✗ (상한 초과)"
  RESULT=1
fi

echo "=== 결과: $([ "$RESULT" -eq 0 ] && echo 'PASS — 동시 enrich 중에도 cron이 바운드 내 락 획득' || echo 'FAIL') ==="
exit "$RESULT"
