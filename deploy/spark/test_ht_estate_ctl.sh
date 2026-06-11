#!/usr/bin/env bash
# ht-estate-ctl 키리스 검증 (ops-sudo PART4) — root 불요. systemctl/journalctl/install을 PATH stub으로
# 가로채 래퍼의 **보안 경계**를 확인: 불량/임의 unit 거부 · injection 거부 · 미지 서브커맨드 거부 ·
# 여분 인자 거부 · install 소스 하드코딩(인자 유래 아님) · 정상 unit 디스패치 인자 정확.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
WRAP="$HERE/ht-estate-ctl"

BIN="$(mktemp -d)"; LOG="$BIN/calls.log"
for tool in systemctl journalctl; do
  cat > "$BIN/$tool" <<EOF
#!/usr/bin/env bash
echo "$tool \$*" >> "$LOG"
exit 0
EOF
  chmod +x "$BIN/$tool"
done
export PATH="$BIN:$PATH"

pass=0; fail=0
ok()   { pass=$((pass+1)); echo "  ✓ $1"; }
bad()  { fail=$((fail+1)); echo "  ✗ $1"; }

# 거부되어야(exit≠0) — 보안 경계
expect_reject() { # desc, args...
  local desc="$1"; shift
  if bash "$WRAP" "$@" >/dev/null 2>&1; then bad "거부 실패: $desc"; else ok "거부: $desc"; fi
}
# 허용(exit0) + 로그에 기대 인자 포함
expect_dispatch() { # desc, expected_log_substr, args...
  local desc="$1" want="$2"; shift 2
  : > "$LOG"
  if bash "$WRAP" "$@" >/dev/null 2>&1 && grep -qF "$want" "$LOG"; then
    ok "디스패치: $desc → '$want'"
  else
    bad "디스패치 실패: $desc (기대 '$want', 로그: $(cat "$LOG" 2>/dev/null))"
  fi
}

echo "[보안 경계 — 거부]"
expect_reject "임의 타테넌트 unit"            restart sshd.service
expect_reject "ht-estate 아닌 unit"           restart ml-hub.service
expect_reject "command injection(;)"          restart "ht-estate-api.service; rm -rf /"
expect_reject "경로 traversal"                status "../../etc/passwd"
expect_reject "대문자/공백 unit"              restart "ht-estate Api.service"
expect_reject "미지 서브커맨드"               frobnicate ht-estate-api.service
expect_reject "여분 인자(restart-api)"        restart-api ht-estate-api.service
expect_reject "여분 인자(restart-web)"        restart-web ht-estate-web.service
expect_reject "여분 인자(restart)"            restart ht-estate-api.service extra
expect_reject "install-units 제거(무비번 표면서 사라짐→미지로 거부)" install-units
expect_reject "install-units +인자도 미지로 거부"  install-units /tmp/evil
expect_reject "인자 없는 restart"             restart
expect_reject "빈 호출"

echo "[정상 디스패치]"
expect_dispatch "restart-api"        "systemctl restart ht-estate-api.service"        restart-api
expect_dispatch "restart-web"        "systemctl restart ht-estate-web.service"        restart-web
expect_dispatch "restart web (generic)" "systemctl restart ht-estate-web.service"     restart ht-estate-web.service
expect_dispatch "restart timer"      "systemctl restart ht-estate-poi.timer"          restart ht-estate-poi.timer
expect_dispatch "status service"     "systemctl status ht-estate-api.service"         status ht-estate-api.service
expect_dispatch "enable --now"       "systemctl enable --now ht-estate-enrich.timer"  enable ht-estate-enrich.timer
expect_dispatch "disable"            "systemctl disable ht-estate-ingest.timer"       disable ht-estate-ingest.timer
expect_dispatch "reload"             "systemctl daemon-reload"                        reload
expect_dispatch "logs"               "journalctl -u ht-estate-poi.service -n 200"     logs ht-estate-poi.service

rm -rf "$BIN"
echo "----"
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ] || exit 1
echo "✅ ht-estate-ctl 키리스 검증 green"
