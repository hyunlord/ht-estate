#!/bin/sh
# 비-아파트 도심-우선 geocode 백필 드라이버 (P5-1b-run · OPS · 멀티데이)
#
# geocode는 Kakao rate-bound 멀티데이(~1–2일+)라 거래 cron 공존이 핵심. backfill_coords는 글로벌·
# 바운드 불가 → geocode_nonapt.py(lat-NULL nonapt·도심우선·LIMIT개·증분커밋)를 **청크당 공유락 +
# 청크 사이 양보**로 반복한다. 양보창에서 거래 cron tick이 신규월을 점진 적재(resumable·무유실).
# Kakao 한도(stopped=1)면 백오프 후 재시도(쿼터 회복 대기). remaining=0이면 완료.
#
# usage: backfill_geocode.sh [limit] [yield_sec] [interval] [stop_backoff_sec]
set -eu

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UV="$(command -v uv 2>/dev/null || echo /opt/homebrew/bin/uv)"
LOCK="$APP_DIR/data/.ingest.lock"            # 거래 cron과 동일 공유 락(SQLite 쓰기 직렬화)
LOG="$APP_DIR/data/geocode_backfill.log"

LIMIT="${1:-300}"            # 청크당 geocode 수 — 락 보유 시간 바운드(작게=cron 공존↑)
YIELD="${2:-3}"             # 청크 사이 양보(초) — 신규월 cron tick에 빈-락 창
INTERVAL="${3:-0.25}"       # Kakao 호출 간격
STOP_BACKOFF="${4:-1800}"  # Kakao 한도 시 백오프(초) — 쿼터 회복 대기 후 재시도

cd "$APP_DIR"

HOLDING=0
cleanup() { [ "$HOLDING" = 1 ] && rm -f "$LOCK"; }   # 우리가 보유 중일 때만 해제(cron 락 오삭제 방지)
trap cleanup EXIT INT TERM

echo "$(date '+%F %T') [geocode-start] limit=$LIMIT yield=${YIELD}s interval=$INTERVAL backoff=${STOP_BACKOFF}s" >> "$LOG"

while true; do
  # 공유 락 acquire(spin) — cron idle tick은 곧 release, 신규월이면 limit-bounded 후 release.
  tries=0
  while ! /usr/bin/shlock -f "$LOCK" -p "$$" 2>/dev/null; do
    tries=$((tries + 1))
    if [ "$tries" -ge 60 ]; then
      echo "$(date '+%F %T') [defer] cron 120s+ 점유 — 양보 후 재시도" >> "$LOG"
      break
    fi
    sleep 2
  done
  [ "$tries" -ge 60 ] && { sleep "$YIELD"; continue; }
  HOLDING=1

  out="$("$UV" run python scripts/geocode_nonapt.py --limit "$LIMIT" --interval "$INTERVAL" 2>>"$LOG")"
  rm -f "$LOCK"; HOLDING=0          # release → 청크 사이 양보 창 OPEN
  echo "$(date '+%F %T') $out" >> "$LOG"

  remaining="$(printf '%s' "$out" | sed -n 's/.*remaining=\([0-9]*\).*/\1/p')"
  stopped="$(printf '%s' "$out" | sed -n 's/.*stopped=\([0-9]*\).*/\1/p')"
  geocoded="$(printf '%s' "$out" | sed -n 's/.*geocoded=\([0-9]*\).*/\1/p')"

  if [ "$remaining" = "0" ]; then
    echo "$(date '+%F %T') [geocode-done] 잔여 0 — 완료" >> "$LOG"
    break
  fi
  # 진척 0(geocoded=0) + 한도 아님(stopped=0) = 큐 앞단이 전부 Kakao 무결과(영구 미해결)
  # → 결정론 도심우선 정렬이라 매 청크 같은 미해결분 재시도 = 무한루프. 중단(잔여=미해결).
  if [ "$geocoded" = "0" ] && [ "$stopped" != "1" ]; then
    echo "$(date '+%F %T') [geocode-done] 진척 0 — 잔여 ${remaining}건은 geocode 무결과(미해결), 중단" >> "$LOG"
    break
  fi
  if [ "$stopped" = "1" ]; then
    echo "$(date '+%F %T') [backoff] Kakao 한도 추정 — ${STOP_BACKOFF}s 후 재시도(resume)" >> "$LOG"
    sleep "$STOP_BACKOFF"
  else
    sleep "$YIELD"                   # 신규월 cron tick이 이 창에 락 획득 가능
  fi
done
