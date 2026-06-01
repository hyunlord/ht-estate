# SPIKE-floorplan-live — 평면도 라이브 1회 (go/no-go 확정)

> R3-ops 라이브 실행. 스파이크가 환경 제약으로 못 답한 핵심 2개(claude -p 비전·feature 품질)를 확정.

## TL;DR — 권고: **GO (조건부)**
- **claude -p 비전: 동작 확정.** 헤드리스 `claude -p ... --allowedTools Read`가 평면도 이미지를 비전으로
  읽고 객관 feature JSON을 낸다 — gym/pet/review와 같은 **키리스/구독 B0 경로**. (네트워크 복구 후 실측.)
- **feature 품질: ground-truth 3/3 정확** + null 규율 준수 + 점수화 없음(§11).
- **남은 검증**: 실 LH 도면 robustness(DATA_GO_KR 키 여전히 없음 — 다운로드 미수행) + 조인 LH-접두 정규화.
- → 작은 기능화 진행하되, **실 LH 표본 검증을 게이트로** 두고 스케일.

## 환경 (전 스파이크 대비 변화)
| 전제 | 전 스파이크 | 지금 |
|---|---|---|
| 네트워크 egress | 격리(reset) | **복구**(data.go.kr 200) |
| `claude` CLI 구독 헤드리스 | 미검증 | **동작**(`claude -p`→PONG, 인증 OK) |
| DATA_GO_KR 키 | 없음 | **여전히 없음** → LH 15037046 다운로드 **미수행** |

→ vision·feature 품질은 **라이브 확정**. LH 실 다운로드·조인 커버리지(실데이터)는 키 없어 미수행.
키 부재를 메우려 **합성 평면도(알려진 ground truth)** 로 vision+품질을 *통제 검증*했다(실 도면 robustness는 별개).

## 1. claude -p 비전 — **동작함 (핵심 미지수 해소)**
- 메커니즘: 이미지를 임시 PNG로 쓰고 `claude -p "<프롬프트> 파일:<path> Read로 추출" --allowedTools Read`.
  Claude Code **Read 도구가 이미지를 비전으로 읽음**. 키 불필요(구독). 3회 호출 전부 JSON feature 반환.
- ∴ 비전 경로는 **API 키/로컬 VLM 불필요** — 기존 enrich(B0)와 동일 인프라로 간다. **no-go 분기 해소.**

## 2. feature 품질 — **ground-truth 3/3, 점수화 없음, null 규율 준수**
표본 = 합성 평면도(룸 라벨·N화살표·발코니 통제). 산출물: `docs/reports/floorplan-live/`
(`fp*.png` 이미지 + `extractions.json` — **Web 육안 대조용**).

| 이미지 | ground truth | claude -p 추출 | 일치 |
|---|---|---|---|
| `fp1_3bay_panstan_south.png` | bay 3·남향·판상형 | bay **3**·**남향**·**판상형** | ✓ |
| `fp2_2bay_panstan_south.png` | bay 2·남향·판상형 | bay **2**·**남향**·**판상형** | ✓ |
| `fp3_tower_noorient.png` | bay null·향 **null**·타워형 | bay **null**·향 **null**·**타워형** | ✓ |

- **evidence 정확**(예 FP1: "N↑ 화살표가 위쪽=북쪽이라 발코니측 전면은 남향, 일자형 직사각 판상").
- **null 규율 준수**(FP3: 발코니 없음→bay null, 화살표 없음→orientation null — 추정·환각 안 함).
- **점수화 없음**(§11): "좋다/넓다/추천" 류 주관 판단 0 — 객관 feature만.
- ⚠ caveat: 합성 도면은 라벨·화살표가 명확. **실 LH 도면**(스캔·치수선·발코니 미표기·다양한 방위표)은
  더 어렵다 → 실 도면 robustness는 키 확보 후 `--run`으로 별도 확인 권고(특히 orientation·bay).

## 3. K-apt 조인 — 미수행(키), 메커니즘은 검증됨(전 스파이크)
- 실 LH 인벤토리 없어 커버리지 수치 미산출(DATA_GO_KR 키 필요).
- 전 스파이크에서: P2-4 fuzzy 재사용 self-test 통과 · LH/주공/휴먼시아류 complex **133개** 로컬 ·
  **비대칭 finding**(LH 접두 sim 0.83<0.85 → 정규화 확장 필요). → 조인은 *feasible*, 기능화 시 정규화 보강.

## 4. go/no-go
**GO (조건부)** — 비전 동작·feature 품질·점수화 금지·null 규율이 라이브로 확인됨. 다음:
1. **작은 기능화**: `enrich_floorplan` 속성을 ATTR_CONFIG에 추가(gym/pet/review 패턴) · parse가 feature
   규율 강제(향/bay null 허용·점수화 거부) · enrichment 기록(value=JSON feature) · 카드 **FloorplanRow
   (표시 전용 — 랭킹 아님, gym/pet과 동형)** · 조인 정규화에 **LH/주공 접두 처리**(비대칭 finding).
2. **실 LH 검증 게이트**: 키 확보 후 `--run --limit 10`으로 *실 도면* robustness(orientation·bay 정확도)
   + 조인 커버리지 확정 → 통과해야 스케일(전국·큰 표본).
3. VLM scoring calibration 신뢰 금지(설계 §11) — feature-only 불변.

## 산출물 (Web 검증용)
- `docs/reports/floorplan-live/fp1·fp2·fp3.png` — 표본 평면도(합성, ground truth 명시).
- `docs/reports/floorplan-live/extractions.json` — ground_truth ↔ extracted 쌍(육안 대조).
- 비전 동작 = 위 3건이 실제 `claude -p` 호출 산출물(키리스 구독 경로).

## 정직한 한계
- 합성 도면(통제) — 실 LH 도면 robustness는 키 확보 후 확정(미수행).
- 조인 커버리지 수치 = 실 LH 인벤토리 필요(미수행). 메커니즘·표적(133)·정규화 gap은 확인됨.
- 결론: **비전·품질 = go 신호 명확**. 스케일 전 실-LH 게이트 1회.
