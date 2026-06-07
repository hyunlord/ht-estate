# ht-estate 공유 락 — 결정론 공존 acquire (coexist-1)
#
# 거래 cron과 비시급 enrich가 같은 쓰기 락(.ingest.lock)을 공유한다(SQLite 단일 writer 직렬화·
# data.go.kr 동시호출 0). 문제(enrich-1b 실측): enrich가 배치 단위로 락을 ~82% 보유하고
# 배치 사이 ~2s만 해제 → cron의 **단발 shlock**(tick당 1회·무재시도)이 그 짧은 해제창을 5/5 놓쳐
# starve. 확률적 양보(sleep 튜닝)는 불충분 확정.
#
# 해법: cron이 **바운드 대기-재시도**로 락을 *기다린다* → enrich의 기존 배치-해제창을 확실히 잡는다.
# 대기 상한(WAIT_SEC) < tick 간격(15분)이라 pile-up 없음. **sourced 함수**로 호출자 프로세스에서
# 돌아 `$$`(PID)가 보유자와 일치(shlock PID 의미 보존) — 별도 자식 프로세스로 빼면 PID가 죽어
# 락이 탈취되므로 반드시 source.
#
# acquire_shared_lock LOCK TTL_MIN WAIT_SEC RETRY_SEC LOG → 0(획득) / 1(상한 초과·미획득)
acquire_shared_lock() {
  _lock="$1"; _ttl_min="$2"; _wait_sec="$3"; _retry_sec="$4"; _log="$5"
  _deadline=$(( $(date +%s) + _wait_sec ))
  while : ; do
    # stale 가드(매 재시도): TTL 초과 락만 제거(살아있는 작업 락은 mtime 갱신과 무관히 보존 —
    # 단, shlock는 죽은 PID 락을 자동 탈취하므로 이 가드는 SIGKILL 등 trap 우회분 보조용).
    if [ -f "$_lock" ] && [ -n "$(find "$_lock" -mmin "+$_ttl_min" 2>/dev/null)" ]; then
      echo "$(date '+%F %T') [stale] ${_ttl_min}분 초과 락 제거" >> "$_log"
      rm -f "$_lock"
    fi
    if /usr/bin/shlock -f "$_lock" -p "$$"; then
      return 0
    fi
    [ "$(date +%s)" -ge "$_deadline" ] && return 1
    sleep "$_retry_sec"
  done
}
