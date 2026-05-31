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
# 게이트는 키리스·결정론: web 빌드에서 Kakao JS 키를 비운다(Next는 process.env가
# .env.local보다 우선 → e2e가 placeholder 경로로 결정론적). 라이브 지도는 `next dev`.
export NEXT_PUBLIC_KAKAO_JS_KEY :=

.PHONY: gate gate-api gate-web gate-e2e \
        ruff-api pyright-api pytest-api api-run \
        lint-web build-web typecheck-web e2e-web \
        load-gym load-pet auto-enrich ingest-seoul ingest-nationwide \
        clean

# 데이터 적재/시드 스크립트 — apps/api에서 실행(스크립트가 _bootstrap으로 sys.path 처리 →
# PYTHONPATH 불필요). cron은 이 타겟이나 스크립트를 직접 호출하면 된다. ARGS로 인자 전달.
ATTR ?= both
LIMIT ?= 20

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

## ───────── 데이터 적재/enrichment (키 필요 — 게이트 밖) ─────────
load-gym:
	cd $(API_DIR) && uv run python scripts/load_gym_seed.py $(ARGS)

load-pet:
	cd $(API_DIR) && uv run python scripts/load_pet_seed.py $(ARGS)

# enrichment 자동 prefill(cron'd claude -p, 구독 인증). make auto-enrich ATTR=gym LIMIT=20
auto-enrich:
	cd $(API_DIR) && uv run python scripts/auto_enrich.py --attribute $(ATTR) --limit $(LIMIT) $(ARGS)

ingest-seoul:
	cd $(API_DIR) && uv run python scripts/ingest_seoul.py $(ARGS)

ingest-nationwide:
	cd $(API_DIR) && uv run python scripts/ingest_nationwide.py $(ARGS)

clean:
	rm -rf $(WEB_DIR)/.next $(WEB_DIR)/test-results $(WEB_DIR)/playwright-report
	rm -rf $(API_DIR)/.pytest_cache $(API_DIR)/.ruff_cache
