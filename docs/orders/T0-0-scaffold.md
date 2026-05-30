# 의뢰서 T0-0 — 모노레포 스캐폴드 + 도구체인 + 게이트 그린

## 목표
ht-estate를 모노레포로 세우고 두 앱(`apps/api` Python·FastAPI, `apps/web` Next.js·TS)의 도구체인과 객관 게이트를 깐다. **이 티켓 종료 시 모든 게이트가 green**이어야 한다 — 빈 통과가 아니라 최소 헬스/스모크 슬라이스로 증명. 이후 모든 티켓이 이 위에서 검증된다. Phase 0 기능 로직은 포함하지 않는다.

## 범위
- IN:
  - 모노레포 레이아웃 + 루트 `Makefile` (게이트 집계)
  - `apps/api`: uv + FastAPI, `/health` 엔드포인트, ruff·pyright·pytest 설정 + 통과 테스트 1개
  - `apps/web`: Next.js(TS, app router) + ESLint, 최소 페이지, Playwright 스모크 1개(렌더 + 콘솔에러 0)
  - `CLAUDE.md` (영구 컨텍스트 + 게이트 명령 + 설계원칙)
  - `.gitignore` (Python·Node·macOS·`.omc/`)
- OUT:
  - MOLIT/K-apt 클라이언트, DB 스키마, 퍼지 조인, 지도, 필터 — 전부 T0-1+ 소관
  - 배포·GitHub Actions CI — 이번 범위 아님 (게이트는 로컬 `make gate`로 충분)

## 컨텍스트
- 설계: `docs/realty-agent-design.md` (§3 구조, §10 빌드 순서)
- 프로토콜: `docs/HARNESS.md` (§6.2 표준, §7 plan-debate-challenge, §8 검증, §9 리턴팩)
- 두 문서 모두 레포에 있으니 직접 읽을 것.
- 스택: 모노레포 / `apps/api`=Python 3.12+ · FastAPI · uv / `apps/web`=Next.js · TS · Node LTS / 게이트 api=ruff+pyright+pytest, web=eslint+타입체크+build / 화면=Playwright

## 변경 예상 파일 (가이드, 절대 아님)
```
Makefile
CLAUDE.md
.gitignore
apps/api/pyproject.toml
apps/api/app/main.py
apps/api/tests/test_health.py
apps/web/            # create-next-app 산출물
apps/web/playwright.config.ts
apps/web/tests/smoke.spec.ts
```

## 수용 기준 (DoD — 객관·체크가능)
- [ ] `apps/api`·`apps/web` 구조 존재, 각 앱 독립 부팅 (api: uvicorn, web: next build)
- [ ] `make gate-api` green: ruff(무오류) + pyright(무오류) + pytest(통과, `/health` 테스트 포함)
- [ ] `make gate-web` green: eslint(무오류) + 타입체크(무오류) + `next build` 성공
- [ ] Playwright 스모크 통과: 홈 렌더 + 핵심 엘리먼트 1개 존재 + 콘솔 에러 0 (스크린샷 첨부)
- [ ] `make gate` (전체 집계) 한 방에 green
- [ ] `CLAUDE.md`에 게이트 명령 + docs 포인터 + 설계원칙(provenance·전국/Postgres 승급·lazy 후보限定·VLM feature-only) 명시
- [ ] `.gitignore`에 `__pycache__/`·`.venv/`·`node_modules/`·`.next/`·`dist/`·`.DS_Store`·`.env`·`.omc/` 포함

## 검증 요구
- 객관 게이트: api(ruff·pyright·pytest) + web(eslint·타입체크·build) + Playwright 스모크 — 전부 green, raw 출력 첨부
- 화면 검증: **Y** — 홈 스크린샷 + Playwright 콘솔 로그
- Web 독립 검증: 브랜치 clone 후 `make gate` 재실행 + PR diff 대조 (구속력)

## 프로토콜
- §7 PLAN → DEBATE → CHALLENGE 수행 후 구현. DEBATE에서 최소 다룰 것:
  - 모노레포에 루트 task runner가 필요한가(Makefile로 충분 vs Turborepo 등)
  - Python 레이아웃 (src-layout vs flat)
  - 타입체크 pyright vs mypy
  - web 타입체크를 별도 `tsc --noEmit`로 뺄지 `next build`에 맡길지
- §8.2 루브릭 self-verify(점수마다 근거 첨부) 후 push. 합계 ≥95 + 차원 하한 충족 아니면 Web에 올리지 말 것.

## 산출물 / 반환
- 브랜치 `chore/T0-0-scaffold`, PR.
- 리턴팩(§9): PR/diff URL · 게이트 raw 출력(api·web·playwright) · 홈 스크린샷 · 루브릭 스코어카드(근거) · 미해결/결정필요 · 다음 제안.
