#!/bin/sh
# 비-아파트(연립RH·오피스텔Offi) 전월세 헤비 적재 드라이버 (P5-1b-run · OPS · 미커밋 ops 도구)
#
# STEP0 근거: ingest_nationwide(app.ingest) 경로엔 **공유 락이 없다**(.ingest.lock은 cron_ingest.sh
#   래퍼에만, 한 invocation 통째 보유). nonapt_rent는 셀당 release도, inter-batch 양보도 없음
#   (refill 양보픽스는 무관 경로). → 여기서 **청크단위 락 + 청크 사이 양보**를 공급해 거래 cron과
#   직렬화(동시 data.go.kr 0)하고 신규월 발생 시 starve를 청크 1개로 바운드한다.
# 또 nonapt_rent는 pending_regions 게이팅이 없어(--limit이 region을 진행 안 시킴) → **명시 --regions**
#   청크가 필수(안 그러면 매 pass가 첫 N개 region만 재선택).
# 멱등·resumable: 셀 레저({stage}_{rowhouse,officetel}) self-skip → 언제든 중단·재실행 안전.
#
# 전월세(nonapt_rent)·매매(nonapt_sale) 공용 — 적재 stage는 STAGE 환경변수로(기본 nonapt_rent).
#   STAGE=nonapt_sale ./backfill_nonapt.sh 8 3 0.6   # 매매 적재(P5-1b-3-run)
# usage: [STAGE=nonapt_rent|nonapt_sale] backfill_nonapt.sh [chunk] [yield_sec] [interval] [months]
set -eu

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UV="$(command -v uv 2>/dev/null || echo /opt/homebrew/bin/uv)"
LOCK="$APP_DIR/data/.ingest.lock"            # 거래 cron과 동일 공유 락
LOG="$APP_DIR/data/nonapt_backfill.log"
CODES="$APP_DIR/data/regions/sigungu_kr.csv"

CHUNK="${1:-8}"          # 청크당 region 수 — 락 보유 시간 바운드
YIELD="${2:-3}"          # 청크 사이 양보(초) — 신규월 cron tick에 빈-락 창
INTERVAL="${3:-0.6}"     # data.go.kr 호출 간격(운영tier·burst 안전)
MONTHS="${4:-}"          # YYYYMM 범위/목록(빈값=최근 12개월)
STAGE="${STAGE:-nonapt_rent}"   # 적재 stage(nonapt_rent=전월세 기본 · nonapt_sale=매매). 레저도 kind별 분리.

case "$STAGE" in
  nonapt_rent|nonapt_sale) ;;
  *) echo "STAGE는 nonapt_rent 또는 nonapt_sale (받음: $STAGE)" >&2; exit 2 ;;
esac

cd "$APP_DIR"

HOLDING=0
cleanup() { [ "$HOLDING" = 1 ] && rm -f "$LOCK"; }   # 우리가 보유 중일 때만 해제(cron 락 오삭제 방지)
trap cleanup EXIT INT TERM

# 청크 파일 생성: region 코드(헤더 제외)를 CHUNK개씩 콤마 결합한 줄
CHUNKFILE="$(mktemp)"
trap 'cleanup; rm -f "$CHUNKFILE"' EXIT INT TERM
tail -n +2 "$CODES" | cut -d, -f1 | awk -v n="$CHUNK" '
  { buf = buf (cnt++ % n ? "," : "") $0; if (cnt % n == 0) { print buf; buf = "" } }
  END { if (buf != "") print buf }
' > "$CHUNKFILE"

TOTAL=$(tail -n +2 "$CODES" | wc -l | tr -d ' ')
NCHUNK=$(wc -l < "$CHUNKFILE" | tr -d ' ')
MONTH_ARG=""
[ -n "$MONTHS" ] && MONTH_ARG="--months $MONTHS"
echo "$(date '+%F %T') [nonapt-start] stage=$STAGE regions=$TOTAL chunks=$NCHUNK(${CHUNK}ea) yield=${YIELD}s interval=$INTERVAL months=${MONTHS:-recent12}" >> "$LOG"

idx=0
while IFS= read -r chunk_codes; do
  idx=$((idx + 1))
  [ -z "$chunk_codes" ] && continue

  # 공유 락 acquire(spin) — cron(idle tick은 곧 release, 신규월이면 limit-bounded 후 release)
  tries=0
  while ! /usr/bin/shlock -f "$LOCK" -p "$$" 2>/dev/null; do
    tries=$((tries + 1))
    if [ "$tries" -ge 60 ]; then
      echo "$(date '+%F %T') [defer] chunk $idx/$NCHUNK — cron 120s+ 점유, 다음 실행에서 재개" >> "$LOG"
      break
    fi
    sleep 2
  done
  [ "$tries" -ge 60 ] && continue   # 양보(레저로 다음 실행 재개)
  HOLDING=1

  echo "$(date '+%F %T') [chunk $idx/$NCHUNK] $STAGE regions=$chunk_codes" >> "$LOG"
  set +e
  "$UV" run python scripts/ingest_nationwide.py \
    --stages "$STAGE" --resume --regions "$chunk_codes" --interval "$INTERVAL" $MONTH_ARG \
    >> "$LOG" 2>&1
  rc=$?
  set -e

  rm -f "$LOCK"; HOLDING=0          # release → 청크 사이 양보 창 OPEN
  echo "$(date '+%F %T') [chunk $idx/$NCHUNK done rc=$rc] — ${YIELD}s 양보" >> "$LOG"
  sleep "$YIELD"                     # 신규월 cron tick이 이 창에 락 획득 가능
done < "$CHUNKFILE"

echo "$(date '+%F %T') [nonapt-done] $NCHUNK chunks 처리(레저 self-skip 포함)" >> "$LOG"
