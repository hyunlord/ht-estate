# ht-estate — 개발 하네스 (v1)

Claude Web(계획·검증) ↔ Claude Code(구현) ↔ 사람(운반·중재) 사이를 도는 작업 루프. 모든 변경은 `plan → debate → challenge → code → 이중검증 → merge`를 통과한다.

**핵심 불변식: 구현자는 자기 작업을 최종 승인하지 않는다.**

---

## 1. 역할 (경계)

- **Claude Web** — planner & **최종 검증자(authoritative)**. 의뢰서 작성, 적용된 실제 코드 독립 검증, 다음 결정. 채팅 컨텍스트는 경계를 넘지 못함 → 레포 + 이 채팅으로만 전달된다.
- **Claude Code** — 구현자. `CLAUDE.md` + 의뢰서를 읽고 `plan → debate → challenge → code → self-verify → push`. self-verify는 사전 스크리닝이지 최종 게이트가 아니다.
- **사람(rexxa)** — 운반(의뢰서 → Code, 리턴팩 → Web) + 에스컬레이션 시 중재자.

---

## 2. 전달 통로 (transport)

- 레포: **public** — `github.com/hyunlord/ht-estate` / 로컬 `/Users/rexxa/github/ht-estate`
- **의뢰서**: 여기서 작성한 markdown → `docs/orders/T<id>-<slug>.md`로 저장 후 Claude Code에 "이거 읽고 시작" (또는 채팅에 붙여넣기)
- **리턴팩**: Claude Code가 브랜치/PR push + 구조화 리포트 출력 (§9)
- **Web 검증**: public이므로 실제 코드를 직접 fetch한다 — 자기보고가 아니라 진짜 diff로 본다.
  - PR diff: `https://github.com/hyunlord/ht-estate/pull/<n>.diff`
  - 커밋 diff: `https://github.com/hyunlord/ht-estate/commit/<sha>.diff`
  - raw 파일: `https://raw.githubusercontent.com/hyunlord/ht-estate/<branch>/<path>`
- **영구 컨텍스트**: 레포 루트 `CLAUDE.md` → `docs/realty-agent-design.md` + `docs/HARNESS.md`를 가리킨다. 의뢰서는 task만 얇게.

---

## 3. 레포 레이아웃 (seed)

```
CLAUDE.md                          # 영구 지시 (design+harness 포인터, 게이트 명령어, 스택)
docs/
  realty-agent-design.md           # 시스템 설계
  realty-agent-architecture.html   # 아키텍처 다이어그램
  HARNESS.md                       # 이 문서
  orders/T<id>-<slug>.md           # 의뢰서 아카이브
  reports/T<id>-<slug>.md          # 리턴팩 아카이브
<code tree>                        # 스택은 T0-0에서 확정
<toolchain>                        # test · lint · typecheck · build · Playwright(화면검증)
```

---

## 4. 루프

0. **(1회) seed** — 레포에 `CLAUDE.md`, `docs/`, 도구체인(테스트·린트·타입체크·빌드, 화면검증용 Playwright) 깔기. 이후 게이트가 돌 수 있게 됨.
1. **여기서 논의.**
2. **Web가 의뢰서 작성** (§6 템플릿).
3. → Claude Code.
4. **Claude Code: PLAN → DEBATE → CHALLENGE** (§7). 통과 못하면 re-plan (상한 2).
5. **challenged plan대로 구현.** 브랜치 `feat/T<id>-<slug>`.
6. **Claude Code self-verify** (사전 스크리닝, §8): 객관 게이트 전부 + 루브릭 근거첨부. 실패면 fix 또는 replan (redo 상한 3) — **Web 부르기 전에 자체 해결.**
7. **push** (브랜치/PR) + 리턴팩 출력 (§9).
8. **Web 독립 검증** (구속력, §8.3): 실제 diff·raw·스크린샷으로 게이트 재실행 + 재채점.
   - 통과 → merge 승인 + 요약.
   - 실패 → redo(구현 문제 → 5) vs replan(접근 문제 → 4) 결정 + 후속 의뢰서.
9. **통과 시**: merge, 요약 + 스코어카드를 여기로.
10. **다음 작업/수정 논의** → 다음 의뢰서.

> **redo vs replan**: 구현이 플랜과 다름/버그 → **redo(5)**. 맞게 구현됐는데 결과물이 틀린 방향(플랜이 잘못) → **replan(4)**.

---

## 5. 루프 제어

- re-plan ≤ 2, redo ≤ 3 → 초과 시 **사람에게 에스컬레이션** (자동 thrash 방지).
- **Web verify가 항상 최종.** Code self-verify는 "≥95 아니면 Web에 올리지 마라" 게이트.
- **한 의뢰서 = 한 티켓 = 한 PR.** 범위를 작게 유지 — 검증 표면이 작아야 검증이 의미 있다.

---

## 6. 의뢰서 (Web → Code)

### 6.1 트리아지 — S vs M·L
- **경량(S)**: 런타임 동작·데이터·UI 거동을 *바꿀 수 없는* 변경 — 문서·주석·포맷, .gitignore/LICENSE/설정 추가(게이트로 검증되는 것), 의존성 핀(테스트 그린), 순수 리네임(레퍼런스 동시 갱신).
- **표준(M·L)**: 로직/데이터/쿼리/UI 거동을 건드리면 줄 수 무관하게 이쪽.
- 판정은 **거동 기준(줄 수 아님)**, 애매하면 표준으로(보수적).
- **S 협약**: §7(plan-debate-challenge)·§8.2(100점 루브릭) **생략**. §8.1 객관 게이트 + §8.3 Web verify(quick diff)는 **유지**. ("사소"가 빌드 깨먹는 게 가장 흔한 사고)
- id: feature = `T0-x`, 잡일(S) = `C<n>` (번호 충돌 방지).

### 6.2 표준 템플릿 (M·L)

```markdown
# 의뢰서 T<id> — <제목>

## 목표
<한 문단. 무엇을, 왜.>

## 범위
- IN:  <이번에 할 것>
- OUT: <이번에 안 할 것 — 명시적으로>

## 컨텍스트
- 설계: docs/realty-agent-design.md §<번호>
- 의존: <선행 티켓 / 모듈>
- 참고: <공공데이터포털 API 문서 URL 등>

## 변경 예상 파일
<경로 목록 — 가이드일 뿐, 절대 아님>

## 수용 기준 (Definition of Done — 객관·체크가능)
- [ ] <검증 가능한 조건 1>
- [ ] <조건 2>
- [ ] 테스트: <무엇을 커버해야 하나>

## 검증 요구
- 객관 게이트: typecheck / lint / test / build [/ 화면]
- 화면 검증: <Y/N — Y면 무엇을 캡처할지>

## 프로토콜
- §7 plan-debate-challenge 준수 → §8 루브릭 self-verify 후 push.
- 설계 원칙 위반 금지 (특히: provenance 필드 유지 · 전국/Postgres 승급 막지 말 것 · lazy 추출은 후보에만 · VLM은 feature 추출만).

## 산출물 / 반환
- 브랜치 feat/T<id>-<slug>, PR.
- 리턴팩(§9) 형식으로 보고.
```

### 6.3 경량 템플릿 (S)

```markdown
# 의뢰서 <id> [S] — <한 줄 제목>

## 목표
<한 줄>

## 범위
- IN:  <할 것>
- OUT: <안 할 것>

## 수용 기준
- [ ] <체크 1~3개>
- [ ] 객관 게이트 그린 유지

## 반환
- 브랜치 <chore|docs|fix>/<id>-<slug>, PR + 한 줄 요약.
```

---

## 7. PLAN → DEBATE → CHALLENGE

- **PLAN**: 접근, 파일단위 변경, 테스트 계획, 리스크/가정, *수용기준과의 매핑*.
- **DEBATE**: 플랜을 **적대적으로 반박**. 접근이 틀렸나? 엣지케이스 누락? 더 단순한 길? 설계원칙 위반? 수용기준을 못 채우나? — "동의하는 척" 금지, 진짜 약점을 찾는다.
- **CHALLENGE**: plan+debate를 종합한 수정 플랜이 아래를 **전부 yes** 해야 통과:
  - [ ] 모든 수용기준을 충족하는가
  - [ ] 설계 문서/원칙과 충돌 없나
  - [ ] 테스트로 검증 가능한가
  - [ ] 범위 안에 머무나 (OUT 침범 안 함)
  - [ ] DEBATE에서 나온 약점이 해소됐나
  - 하나라도 no → **re-plan**.

---

## 8. 검증 (Verify)

### 8.1 객관 게이트 (binary · 전부 통과 필수 · 우길 수 없음)
- typecheck 무오류
- lint 무오류 (또는 합의된 baseline)
- 테스트 전부 통과 **+ 새 코드에 의미있는 테스트 존재**
- build 성공 / 앱 부팅
- (UI 티켓) Playwright 렌더 + **콘솔 에러 0** + 핵심 엘리먼트 존재 + **스크린샷 첨부**

→ 하나라도 실패면 점수 매기기 전에 **fail**.

### 8.2 루브릭 (정성 · 100점 · 차원별 하한 + 근거 필수)

| 차원 | 배점 | 하한 | 본다 |
|---|---|---|---|
| 완결성 | 30 | **27** | 수용기준 100% 충족, 엣지케이스, 누락 없음 |
| 안정성 | 30 | **27** | 에러 처리, 비결정성/flaky 없음, 테스트가 진짜 검증 |
| 확장성 | 20 | 17 | 설계 원칙 준수 (전국·멀티유저·Postgres·provenance 안 막음) |
| 설계품질 | 20 | 16 | 명료·관용적·과설계 아님, 정규화/조인 모듈 분리 등 |

- **통과 = 합계 ≥ 95 그리고 모든 차원 하한 충족.** (완결성·안정성 하나라도 무너지면 95여도 fail — 화려함으로 안전을 못 산다)
- **근거 규칙**: 점수마다 증거(테스트 출력 라인 / diff 라인 / 스크린샷)를 인용한다. 증거 없으면 그 차원은 하한 미만으로 간주. (LLM 자기채점은 후하다 — 증거로 묶는다)

### 8.3 Web 독립 검증
Code의 스코어카드는 참고용. Web는 실제 diff/raw/스크린샷으로 8.1·8.2를 **다시** 돌려 자체 판정한다. 불일치하면 **Web 판정이 이긴다.**

---

## 9. 리턴팩 (Code → Web)

```markdown
# 리턴팩 T<id>

## 변경 요약
<3~6줄. 무엇을 어떻게.>

## PR / diff
- PR:  <url>
- diff: <url>.diff

## 객관 게이트 (raw 출력 붙임)
- typecheck: <pass/fail + 출력>
- lint:      <…>
- test:      <…>
- build:     <…>
- 화면:      <스크린샷 + 콘솔 로그>   # UI 티켓만

## 루브릭 스코어카드 (증거 첨부)
- 완결성 28/30 — 근거: <…>
- 안정성 28/30 — 근거: <…>
- 확장성 18/20 — 근거: <…>
- 설계품질 17/20 — 근거: <…>
- 합계 91/100 · 하한충족: <Y/N>

## 미해결 / 결정필요
<있으면 나열, 없으면 "없음">

## 다음 제안
<후속 티켓 후보>
```

---

## 10. 첫 사이클

- **T0-0 seed** — 이 하네스 + `CLAUDE.md` + 도구체인(스택 확정 포함) + Phase 0 스캐폴딩.
- 이후 **T0-1 … T0-7** (설계 §10): 공공 API 클라이언트 → 단지정보 파싱 → 실거래 적재 → **퍼지 조인** → 지오코딩 → hard filter API → Kakao 지도.
