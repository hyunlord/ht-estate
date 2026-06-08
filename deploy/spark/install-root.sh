#!/usr/bin/env bash
# migrate-1-spark — 단일 권한(root) 설치 스텝. `sudo bash deploy/spark/install-root.sh`로 실행.
# 비권한 검증(지문/counts/Gemma/shlock 빌드+스모크/API 부팅/search)은 이미 통과한 상태에서,
# 권한이 필요한 것만 모았다: (1) /usr/bin/shlock (2) systemd 유닛 설치·enable·start.
set -euo pipefail
REPO=/home/hyunlord/github/ht-estate
SPARK="$REPO/deploy/spark"

echo "== [1/4] INN-호환 shlock → /usr/bin/shlock =="
test -x "$REPO/deploy/shlock/shlock" || { echo "shlock 바이너리 없음 — 먼저 빌드"; exit 1; }
install -m 755 "$REPO/deploy/shlock/shlock" /usr/bin/shlock
/usr/bin/shlock -f /tmp/.shlock_root_smoke -p $$ && echo "  acquire 스모크 ok" ; rm -f /tmp/.shlock_root_smoke

echo "== [2/4] systemd 유닛 설치 =="
install -m 644 "$SPARK"/ht-estate-api.service     /etc/systemd/system/
install -m 644 "$SPARK"/ht-estate-ingest.service  /etc/systemd/system/
install -m 644 "$SPARK"/ht-estate-ingest.timer    /etc/systemd/system/
install -m 644 "$SPARK"/ht-estate-enrich.service  /etc/systemd/system/
install -m 644 "$SPARK"/ht-estate-enrich.timer    /etc/systemd/system/
systemctl daemon-reload

echo "== [3/4] enable + start =="
systemctl enable --now ht-estate-api.service
systemctl enable --now ht-estate-ingest.timer
systemctl enable --now ht-estate-enrich.timer

echo "== [4/4] docker 부팅 자동시작(gemma --restart unless-stopped 보존) =="
systemctl enable docker.service 2>/dev/null || true

echo "DONE — systemctl status ht-estate-api / list-timers 로 확인"
