# SPIKE — 평면도 PoC (LH 15037046 · VLM feature 추출)

> 성격: HARNESS R2 스파이크 — **findings + go/no-go**, 풀 루브릭 아님. 작은 표본으로 "되나 보자".

## TL;DR — 권고: **조건부 go** (라이브 1회 검증 후 작은 기능화)
파이프라인(parse→VLM→join)의 **로직은 viable**하고 키리스 self-test로 parse+join을 검증했다.
다만 **세 핵심 미지수 중 둘(vision 동작·feature 품질)은 이 환경에서 답할 수 없었다** — 샌드박스가
**키리스 + 네트워크 격리**라 LH 다운로드도 `claude -p` 비전 호출도 불가(finding #0). 하네스를
ops가 **~30분·N=5 표본**으로 한 번 돌리면 go/no-go가 확정된다. 명령·합격신호는 §5.

## Finding #0 — 환경 (스파이크가 처음 부딪힌 벽)
| 전제 | 상태 |
|---|---|
| `claude` CLI | **있음** (2.1.154) |
| data.go.kr 키 | **없음** (.env 없음, env 비어있음) |
| 네트워크 egress | **없음** (data.go.kr 연결 reset, HTTP 000) |
| PIL 등 이미지 라이브러리 | 없음 |

→ LH fetch(키+네트워크)·`claude -p` 비전(네트워크) **둘 다 이 환경에서 실행 불가**.
라이브 검증은 **사용자(ops) 환경**에서. 이 스파이크는 *로직 + 합성 self-test + 실행 레시피*를 낸다.

## 미지수별 결과

### 1. `claude -p` 비전 — **미검증(환경 제약), 메커니즘은 준비됨**
- 답 못함: 네트워크 격리라 `claude -p` 호출 불가.
- 메커니즘(하네스 `extract_features`): 이미지를 임시파일로 쓰고
  `claude -p "<프롬프트> 파일:<path> Read로 열어 추출" --allowedTools Read` —
  Claude Code의 **Read 도구가 이미지를 비전으로 읽는다**(gym/pet/review와 같은 **키리스/구독 B0 경로**).
- 합리적 예측: Claude Code Read는 PNG/JPG 비전을 지원하므로 **viable일 가능성 높음**.
  단 헤드리스 `-p`에서 Read-on-image가 기대대로 동작하는지는 **실측 필요**(이 스파이크의 1순위 검증).
- no-go 분기: 헤드리스 비전이 안 되면 → 비전은 API 키/로컬 VLM 별도 결정(그것도 findings).

### 2. feature 품질 — **미검증(vision 의존), 프롬프트는 §11 가드로 설계**
- 답 못함: 실제 평면도 + 동작하는 vision 필요.
- 프롬프트(`enrich_floorplan.md`): **객관 feature만** — `bay`(전면 실 수)·`orientation`(도면 N표/'남향'
  표기 있을 때만)·`structure`(판상/타워). **점수화·평가 금지**(설계 §11: "좋은 구조"는 사용자 가중치 몫).
  근거 없으면 `null`(환각 금지) + `evidence` 필수.
- 예측(설계 경험 기반): bay·판상/타워는 도면 형태로 *대체로* 추출 가능, **orientation은 도면에
  방위표가 있어야** — LH 도면 방위표 유무가 품질을 가른다. 표본 육안 대조가 필요(Web 검증 항목).

### 3. K-apt 조인 — **부분 답(긍정) + 비대칭 finding**
- **메커니즘 viable**: P2-4 `fuzzy.best_match`+`extract_dong` 재사용 → self-test 통과
  (`수서1단지`→`LH수서1단지` sim 0.90, 무관 단지→None=억지 금지 유지).
- **표적 존재**: 로컬 complex 4799 중 **LH/주공/휴먼시아류 133개**(서울 82·부산 51) —
  `개포주공7단지`·`LH수서1단지(행복주택)`·`휴먼시아` 등. 전부 `legal_addr` 보유(4797/4799) → 주소 narrowing 가능.
- **비대칭 finding(실측)**: LH-접두가 *더 긴* 방향(`LH수서1단지`→`수서1단지`)은 포함부스트 미발동 →
  sim **0.83 < 0.85** → 미매치. **P2-4 정규화에 'LH/주공' 접두 정규화 확장**이 조인 커버리지를 올린다.
- **커버리지 caveat**: 로컬 complex는 강남(private)+부산 위주라 LH 공공주택 비중이 낮다 →
  **실 조인 커버리지는 전국 complex(C20 적재) 진척에 비례**. 표본 커버리지 수치는 라이브 run에서.

## 4. 산출물 (검증된 것 / 라이브 대기)
- `scripts/spikes/floorplan_poc.py` — parse(A)·vision(B)·join(C)·`--selftest`·`--run`. **`--selftest` 키리스 통과**(parse+join). ruff/pyright clean.
- `scripts/prompts/enrich_floorplan.md` — VLM 프롬프트(객관 feature·§11 점수화 금지).
- **검증됨(키리스)**: base64 디코드, 단지명/주소 방어 파싱, 조인 매칭·억지금지, 비대칭 finding.
- **라이브 대기**: 15037046 실 스키마(엔드포인트/필드 라이브 확정), vision 동작, feature 품질, 조인 커버리지 수치.

## 5. 라이브 실행 레시피 (ops · ~30분 · N=5) — go/no-go 확정용
```bash
cd apps/api
# (선행) 전국 complex 적재가 있으면 조인 커버리지↑ — 없으면 강남/부산 LH로 부분 측정
DATA_GO_KR_API_KEY=<키> uv run python scripts/spikes/floorplan_poc.py --run --limit 5
```
표본당 출력: `단지명 · VLM{bay,orientation,structure,evidence} · 조인(complex_id,score)`.
**합격 신호(→ go)**: ① VLM이 JSON feature를 *낸다*(비전 동작) · ② 이미지 육안 대조로 bay/structure가
*대체로 맞다* · ③ 조인이 표본 일부에 *붙는다*(0이 아님) · ④ **점수화 안 함**(feature-only 준수).
**불합격(→ no-go/조건부)**: 비전 무응답(키/로컬모델 결정) · feature가 도면과 무관(품질 미달) · 조인 0(정규화/전국적재 선행).

## 6. go/no-go 권고
**조건부 go** — 로직은 섰고(self-test), 표적(LH complex 133)도 있다. 다음 순서를 권고:
1. **라이브 1회(§5)** 로 vision 동작 + feature 품질 + 조인 커버리지 확정(이 스파이크가 못 답한 핵심).
2. go면 **작은 기능화**: `enrich_floorplan` 속성을 ATTR_CONFIG에 추가(gym/pet/review 패턴) ·
   enrichment 테이블 기록(value=JSON feature) · 카드 FloorplanRow(**표시 전용**, gym/pet처럼 랭킹 아님).
   조인 정규화에 **LH/주공 접두 처리** 포함(비대칭 finding).
3. no-go(비전 안 됨)면 → 비전 경로를 API 키/로컬 VLM으로 재결정(별도 스파이크).

## 7. 범위 밖 (지킴)
전국 적재·랭킹 연동·제품화·큰 표본·enrichment 정식 적재 — 전부 OUT. 점수화 금지(§11) 프롬프트로 인코딩.

## 다음
- **ops: §5 라이브 1회** (go/no-go 확정 — 이게 진짜 다음).
- review cron(P3-1 실수집) / naver 블록셋 재검토 / 전국 적재 마감 — 병행 가능.
