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
        ruff-api pyright-api pytest-api api-run dev \
        lint-web build-web build-web-prod assert-web-key typecheck-web e2e-web \
        load-gym load-pet load-review auto-enrich enrich-cron review-cron \
        ingest-seoul ingest-nationwide clean

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

## ───────── 로컬 개발 런처 (실지도 시각 확인 — 게이트 밖) ─────────
# API(:8000) + web(:3000) 동시 부팅. Ctrl-C로 둘 다 종료(trap kill).
# ⚠ 게이트용 `export NEXT_PUBLIC_KAKAO_JS_KEY :=`(빈값)이 .env.local보다 우선 → dev에선 unset해
#    next dev가 apps/web/.env.local의 NEXT_PUBLIC_KAKAO_JS_KEY를 읽게 한다(없으면 지도 placeholder).
# API가 서빙하는 DB = $(API_DIR)/data/ht-estate.db (이 체크아웃 기준). 실데이터는 적재된 체크아웃에서.
dev:
	@echo "▶ API :8000 + web :3000 — Ctrl-C로 종료 · 브라우저: http://localhost:3000"
	@( cd $(API_DIR) && exec uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 ) & \
	api_pid=$$!; \
	trap 'kill $$api_pid 2>/dev/null' EXIT INT TERM; \
	cd $(WEB_DIR) && unset NEXT_PUBLIC_KAKAO_JS_KEY && npm run dev

## ───────── WEB (Next.js · TypeScript) ─────────
lint-web:
	cd $(WEB_DIR) && npm run lint

build-web:
	cd $(WEB_DIR) && npm run build

# ★ 프로덕션 web 빌드 (kakao-key-build-durable) — 모든 ht-estate-web 재배포는 이 타겟.
#   `unset NEXT_PUBLIC_KAKAO_JS_KEY`로 게이트용 빈 export(L17) override를 제거 → Next가 .env.local
#   실 키를 번들에 인라인(실지도). 빌드 후 키 단언으로 빈-키(키리스) 빌드가 prod로 새지 않게 차단.
#   gate-web/e2e는 빈 키 그대로 유지(결정론) — 두 경로가 서빙 산출물을 안 덮게 분리.
build-web-prod:
	cd $(WEB_DIR) && unset NEXT_PUBLIC_KAKAO_JS_KEY && npm run build
	$(MAKE) assert-web-key

# post-build 키 단언 — 번들에 non-empty Kakao 키 인라인됐는지(빈-키면 loud FAIL).
assert-web-key:
	$(WEB_DIR)/scripts/assert-kakao-key.sh

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

# review 시드 → DB 적재(promote 마지막 단계 — 사람이 spot-audit·seed commit 후 트리거).
load-review:
	cd $(API_DIR) && uv run python scripts/load_review_seed.py $(ARGS)

# enrichment cron — gym/pet/review **staging까지만**(human commit gate: 라이브 DB write·git commit
# 안 함). 사람이 spot-audit 후 promote(seed commit + load-<attr>). make enrich-cron ATTR=gym LIMIT=20
enrich-cron:
	cd $(API_DIR) && uv run python scripts/enrich_cron.py --attribute $(ATTR) --limit $(LIMIT) $(ARGS)

# [DEPRECATED] auto-enrich → enrich_cron(staging-only)로 위임. 무검토 자동 DB write 안 함.
# DB 적재는 사람이 load-<attr>로. 신규 사용은 enrich-cron 권장.
auto-enrich:
	cd $(API_DIR) && uv run python scripts/auto_enrich.py --attribute $(ATTR) --limit $(LIMIT) $(ARGS)

# review 후기 cron(= enrich-cron ATTR=review) — back-compat. make review-cron LIMIT=20
review-cron:
	cd $(API_DIR) && uv run python scripts/review_cron.py --limit $(LIMIT) $(ARGS)

ingest-seoul:
	cd $(API_DIR) && uv run python scripts/ingest_seoul.py $(ARGS)

ingest-nationwide:
	cd $(API_DIR) && uv run python scripts/ingest_nationwide.py $(ARGS)

clean:
	rm -rf $(WEB_DIR)/.next $(WEB_DIR)/test-results $(WEB_DIR)/playwright-report
	rm -rf $(API_DIR)/.pytest_cache $(API_DIR)/.ruff_cache
