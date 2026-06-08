#!/usr/bin/env bash
# shlock 빌드(키리스·무root) — libc만 의존. install-root.sh가 결과 바이너리를 /usr/bin로 설치.
set -euo pipefail
cd "$(dirname "$0")"
gcc -O2 -Wall -Wextra -o shlock shlock.c
echo "built: $(file shlock)"
