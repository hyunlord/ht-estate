#!/usr/bin/env bash
# ht-estate 단일 권한(root) 설치 스텝. `sudo bash deploy/spark/install-root.sh`로 1회 실행.
# 비권한 검증(지문/counts/Gemma/shlock/API/search)은 이미 통과한 상태에서 권한 필요분만 모음:
#   (1) /usr/bin/shlock  (2) systemd 유닛  (3) ops 래퍼 /usr/local/sbin/ht-estate-ctl
#   (4) scoped sudoers(래퍼-only, visudo 검증 후 활성화)  (5) enable/start  (6) docker 부팅.
# 멱등 — 여러 번 실행해도 안전. ops-sudo 이후 restart/enable/status/logs는 `sudo ht-estate-ctl`로
# 무비번 자율(operate-ops). **새/변경 유닛 설치는 이 스크립트 재실행**(사용자 sudo, 드묾) — 무비번
# 래퍼엔 유닛 설치 없음(새 root 유닛 내용은 사람 검토+사람 설치 = escalation 0).
set -euo pipefail
REPO=/home/hyunlord/github/ht-estate
SPARK="$REPO/deploy/spark"

echo "== [1/6] INN-호환 shlock → /usr/bin/shlock =="
test -x "$REPO/deploy/shlock/shlock" || { echo "shlock 바이너리 없음 — 먼저 빌드"; exit 1; }
install -m 755 "$REPO/deploy/shlock/shlock" /usr/bin/shlock
/usr/bin/shlock -f /tmp/.shlock_root_smoke -p $$ && echo "  acquire 스모크 ok" ; rm -f /tmp/.shlock_root_smoke

echo "== [2/6] systemd 유닛 설치 (ht-estate-*.{service,timer} 전부) =="
shopt -s nullglob
units=("$SPARK"/ht-estate-*.service "$SPARK"/ht-estate-*.timer)
[ ${#units[@]} -gt 0 ] || { echo "유닛 소스 없음"; exit 1; }
install -m 644 -o root -g root "${units[@]}" /etc/systemd/system/
systemctl daemon-reload
echo "  ${#units[@]} units 설치"

echo "== [3/6] ops 래퍼 → /usr/local/sbin/ht-estate-ctl (root:root 0755) =="
install -m 755 -o root -g root "$SPARK/ht-estate-ctl" /usr/local/sbin/ht-estate-ctl

echo "== [4/6] scoped sudoers → /etc/sudoers.d/ht-estate (visudo -c 검증 후 활성화) =="
# malformed sudoers가 sudo 락아웃을 일으키지 못하게: 임시로 검증 통과 후에만 제자리에 설치.
_tmp_sudoers="$(mktemp)"
install -m 440 -o root -g root "$SPARK/sudoers.d-ht-estate" "$_tmp_sudoers"
if visudo -cf "$_tmp_sudoers" >/dev/null; then
  install -m 440 -o root -g root "$SPARK/sudoers.d-ht-estate" /etc/sudoers.d/ht-estate
  rm -f "$_tmp_sudoers"
  echo "  sudoers 활성화(visudo -c 통과)"
else
  rm -f "$_tmp_sudoers"
  echo "  ✗ sudoers visudo -c 실패 — 설치 건너뜀(락아웃 방지)"; exit 1
fi

echo "== [5/6] enable + start =="
systemctl enable --now ht-estate-api.service
for t in ht-estate-ingest.timer ht-estate-enrich.timer ht-estate-poi.timer \
         ht-estate-unit-type.timer ht-estate-gym-kakao.timer ht-estate-corpus.timer; do
  [ -f "/etc/systemd/system/$t" ] && systemctl enable --now "$t" || true
done
# web: **enable만**(부팅 생존) — `--now` 미사용. 임시 nohup 웹이 아직 :3100 점유(핸드오프 finish서
# kill 후 `ht-estate-ctl restart-web`이 인수) → 설치 시점 즉시 start하면 포트 충돌. enable로 재부팅만 보장.
[ -f "/etc/systemd/system/ht-estate-web.service" ] && systemctl enable ht-estate-web.service || true

echo "== [6/6] docker 부팅 자동시작(gemma --restart unless-stopped 보존) =="
systemctl enable docker.service 2>/dev/null || true

echo "DONE — 확인: sudo -n /usr/local/sbin/ht-estate-ctl status ht-estate-api"
