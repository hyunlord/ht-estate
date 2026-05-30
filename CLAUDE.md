# ht-estate — Claude Code 영구 컨텍스트

조건 검색이 안 되는 단지 정보(헬스장·전용면적·세대당 주차·신축 정도·강아지·구조)를
**공공 API 결정론 레이어 + 온디맨드 확률 레이어**로 합치고, **모든 사실에 출처를 달아**
보여주는 단지 탐색 에이전트.

## 문서 (먼저 읽기)
- 설계: `docs/realty-agent-design.md` — 시스템 구조(§3)·데이터 모델(§4)·퍼지 조인(§5.1)·빌드 순서(§10)
- 하네스: `docs/HARNESS.md` — 작업 루프·PLAN→DEBATE→CHALLENGE(§7)·검증(§8)·리턴팩(§9)
- 아키텍처 다이어그램: `docs/realty-agent-architecture.html`
- 의뢰서/리턴팩 아카이브: `docs/orders/`, `docs/reports/`

## 스택 (모노레포)
- `apps/api` — Python 3.12 · FastAPI · uv (3.12는 `.python-version`으로 핀)
- `apps/web` — Next.js · TypeScript · App Router
- 저장소 기본값 SQLite → 승급 시 Postgres + PostGIS (설계 §2)

## 게이트 (객관 검증 · 머지 전 전부 green이어야)
```
make gate        # 전체 집계: api + web + e2e
make gate-api    # ruff(lint) + pyright(typecheck) + pytest
make gate-web    # eslint + tsc --noEmit(typecheck) + next build
make gate-e2e    # Playwright 스모크 (렌더 + 콘솔에러 0 + 핵심 엘리먼트 + 스크린샷)
make api-run     # uvicorn 단독 부팅 → GET /health
```
- self-verify(§8.2 루브릭 합계 ≥95 + 차원 하한)를 통과 못하면 **push 금지**.
- **핵심 불변식: 구현자는 자기 작업을 최종 승인하지 않는다.** Web 독립 검증(§8.3)이 최종.

## 설계 원칙 (위반 금지)
1. **provenance** — 모든 사실은 필드 단위로 `(값, source_type, source_url, fetched_at, confidence)`를
   들고 다닌다. `enrichment`는 (단지, 속성)당 출처별 다중 행으로 보관. 출처 필드를 제거/축소하지 말 것.
2. **전국·멀티유저·Postgres 승급을 막지 말 것** — 범위는 지역코드/거래유형 추가만으로 전국·전월세로
   넓혀질 수 있어야 하고, 저장소는 SQLite→Postgres 스왑이 가능한 경계를 유지한다.
3. **lazy 추출은 후보에만** — 비싼 LLM/웹 추출(강아지·구조·후기)은 hard filter를 통과한 후보(~20)에만 낸다.
   전국 벌크 추출 금지. (정당화는 신선도가 아니라 후보 수)
4. **VLM은 feature 추출만** — 평면도에서 bay 수·향(남향)·판상/타워 같은 **객관 feature만** 추출한다.
   "좋은 구조" 같은 주관 점수 금지(사용자 가중치로 점수화). VLM scoring calibration은 신뢰하지 않는다.

## 워크플로 (HARNESS)
- 브랜치: feature = `feat/T<id>-<slug>`, 잡일(S) = `chore|docs|fix/<id>-<slug>`. **한 티켓 = 한 PR.**
- 구현 전 PLAN→DEBATE→CHALLENGE(§7) → 구현 → 객관 게이트 + 루브릭 self-verify(§8) → push → 리턴팩(§9).
- re-plan ≤ 2, redo ≤ 3 초과 시 사람에게 에스컬레이션.
- `.omc/`는 개인 오케스트레이션 상태이므로 무시(gitignore).
