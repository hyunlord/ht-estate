# SPIKE-floorplan-realgate — 실 도면 robustness 게이트

> R3-ops 라이브 게이트. 기능화 전 마지막 미지수: **실 도면(messy)에서도 vision이 정확 + null 규율 유지하나.**

## TL;DR — 권고: **GO** (vision robustness 확인) · 단 LH-특이 항목은 키 후
- **실 LH 다운로드 미수행** — DATA_GO_KR 키가 이 환경에 *여전히* 없음(env 빈값·.env 없음). 합성 우회 안 함.
- 대신 **공개 라이선스 실 평면도 2건**(PD 단독주택 + CC BY-SA 실 아파트)으로 *messy 실 도면 robustness*를 닫음.
- 결과: **vision이 실 messy 도면서도 robust + null 규율 우수**(out-of-domain 단독주택을 all-null로 정확히 거부).
- 남은 LH-특이: K-apt 조인 커버리지(실 LH 단지명)·LH 도면 관습 — **키 확보 후 `--run` 1회**(기능화와 병행 가능).

## 환경 (세 번째 확인 — 키는 끝내 없음)
| 전제 | 상태 |
|---|---|
| 네트워크 | UP |
| `claude -p` 헤드리스 비전 | **동작**(앞 라이브서 확인, 본 게이트서도 실 도면 4건 추출) |
| **DATA_GO_KR 키** | **없음**(env 빈값·.env 없음·kakao도 없음) → **LH 15037046 다운로드 불가** |

→ 실 LH는 못 받았다. 게이트의 *핵심 질문*(실 messy 도면 robustness)은 **공개 실 도면 proxy**로 답했다.
LH-특이(조인 커버리지·LH 관습)는 키 의존이라 미수행 — 정직하게 분리해 보고한다.

## 실 도면 vision robustness (proxy, 산출물 `docs/reports/floorplan-realgate/`)
실 평면도 = 치수선·door swing·해치 벽·furniture 심볼·영문 라벨 = 합성보다 훨씬 messy.

| 이미지(실·messy) | 육안 reading | claude -p 추출 | 판정 |
|---|---|---|---|
| `proxy_focsa_apartment.jpeg`(실 apt, 방위표 없음) | 향 null·복도식 슬래브·상단 ~3실 | **bay 3·향 null·판상형** | ✓ 정확: 외기면 3실 카운트, **방위표 없음→향 null(환각 없음)**, 슬래브→판상 |
| `proxy_sample_floorplan.jpg`(미국 단독주택, N표 있음·out-of-domain) | 베이/판상 불해당·향 모호 | **bay null·향 null·structure null** | ✓ **우수**: "한국 아파트 아님→단독주택" 인식, 전부 null + 근거(스키마 강제 환각 거부) |

**핵심 robustness findings**:
1. **messy 실 linework 처리 OK** — 치수선·door swing·영문 라벨을 뚫고 구조를 읽음.
2. **null 규율 우수(실 도면서도)** — 방위표 없으면 향 null, *아파트가 아니면* 전 feature null로 거부.
   단독주택 케이스가 결정적: 스키마에 억지로 맞추지 않고 "불해당→null"을 근거와 함께 반환.
3. **점수화 0**(§11) — 두 건 모두 객관 feature·evidence만, "좋다/추천" 없음.
4. **out-of-domain 안전** — 비-한국·비-아파트 도면을 환각 없이 null 처리(전국·잡다 표본서 중요).

### 한계(정직)
- **LH 15037046 아님** — PD/CC 공개 도면(쿠바 apt·미국 house) proxy. Korean LH 특유 표기(평형·방위표 양식·
  발코니 표기 관습)·**K-apt 조인 커버리지(실 LH 단지명)**는 *미검증* — 키 필요.
- FOCSA "판상형"은 단위 도면 하나로의 합리적 추론(판상/타워는 동(棟) 전체가 더 확실) — 측정값 환각은 아님.
- orientation은 둘 다 null(방위표 없거나 향 단정 불가) — 합성서 방위표 있을 땐 정확히 남향 추출. ∴ 향은
  *방위표 있을 때 추출, 없으면 null* — 실 LH 도면의 방위표 유무가 향 커버리지를 가른다(키 후 확인).

## 누적 근거 (3 스파이크)
- 합성 ground-truth **3/3 정확**(3bay·2bay·타워, null 규율).
- 실 messy proxy **2/2 적절**(실 apt 정확, 단독주택 all-null 거부).
- ∴ **vision viability + feature 품질 + null 규율 = 강한 GO 신호.** 미해결 = LH-특이 조인 커버리지(키).

## go/no-go
**GO** — 기능화 진행 권고. vision은 실 messy 도면서도 robust하고 null 규율을 지킨다(기능화 전 핵심 de-risk 통과).
1. **작은 기능화**: `enrich_floorplan` 속성(ATTR_CONFIG, gym/pet/review 패턴) · parse가 feature 규율 강제
   (bay/향 null 허용·점수화 거부·structure 도메인) · enrichment 기록 · 카드 **FloorplanRow(표시 전용·랭킹 아님)**
   · 조인 정규화에 **LH/주공 접두 처리**(앞 비대칭 finding).
2. **LH-특이 잔여(키 후 1회, 기능화와 병행)**: `--run`으로 ① 실 LH 도면 방위표/관습 robustness ② K-apt 조인
   커버리지(실 LH 단지명, LH접두 정규화 효과). 이건 *viability* 게이트가 아니라 *튜닝/측정* 항목 → 기능화 차단 안 함.
3. VLM scoring calibration 신뢰 금지(§11) — feature-only 불변.

## 산출물 (Web 검증용)
- `docs/reports/floorplan-realgate/{proxy_focsa_apartment.jpeg, proxy_sample_floorplan.jpg}` — 실 도면(라이선스 README).
- `docs/reports/floorplan-realgate/extractions.json` — 육안 ↔ 추출 쌍.
- `docs/reports/floorplan-realgate/README.md` — proxy 명시 + 출처/라이선스.
