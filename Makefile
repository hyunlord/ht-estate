# ht-estate — 루트 게이트 집계기 (objective gates)
#
#   make gate        전체 집계: api + web + e2e
#   make gate-api    ruff + pyright + pytest
#   make gate-web    eslint + tsc --noEmit + next build
#   make gate-e2e    Playwright 스모크 (콘솔에러 0 + 핵심 엘리먼트 + 스크린샷)
#   make api-run     uvicorn 단독 부팅 → GET /health
#
# recipe 줄은 TAB 들여쓰기. cd는 줄 단위 서브셸이므로 && 로 묶는다.

SHELL := /bin/bash
API_DIR := apps/api
WEB_DIR := apps/web
export NEXT_TELEMETRY_DISABLED := 1

.PHONY: gate gate-api gate-web gate-e2e \
        ruff-api pyright-api pytest-api api-run \
        lint-web build-web typecheck-web e2e-web \
        clean

## ───────── API (Python · uv · FastAPI) ─────────
ruff-api:
	cd $(API_DIR) && uv run ruff check .

pyright-api:
	cd $(API_DIR) && uv run pyright

pytest-api:
	cd $(API_DIR) && uv run pytest

gate-api: ruff-api pyright-api pytest-api
	@echo "✅ gate-api green"

api-run:
	cd $(API_DIR) && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

## ───────── WEB (Next.js · TypeScript) ─────────
lint-web:
	cd $(WEB_DIR) && npm run lint

build-web:
	cd $(WEB_DIR) && npm run build

# tsc는 next-env.d.ts(빌드 산출물)가 필요하므로 build-web 선행.
typecheck-web: build-web
	cd $(WEB_DIR) && npm run typecheck

gate-web: lint-web build-web typecheck-web
	@echo "✅ gate-web green"

# Playwright는 production 서버(next start)에 붙으므로 build-web 선행.
e2e-web: build-web
	cd $(WEB_DIR) && npm run test:e2e

gate-e2e: e2e-web
	@echo "✅ gate-e2e green"

## ───────── 집계 ─────────
# build-web은 prereq DAG에서 한 번만 실행된다.
gate: gate-api gate-web gate-e2e
	@echo "✅✅ ALL GATES GREEN"

clean:
	rm -rf $(WEB_DIR)/.next $(WEB_DIR)/test-results $(WEB_DIR)/playwright-report
	rm -rf $(API_DIR)/.pytest_cache $(API_DIR)/.ruff_cache
