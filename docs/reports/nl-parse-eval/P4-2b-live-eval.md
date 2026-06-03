# P4-2b 라이브 eval — NL→spec 파서 (claude -p 구독)

생성: 2026-06-03 · 러너: `claude -p`(구독·키리스, 웹 도구 미승인) · max_turns=2
원본: [`live-eval-raw.json`](./live-eval-raw.json) (7개 대표 질의의 spec/감지/unsupported 전체)

HARNESS §8.4 deferral: 키리스 게이트(ruff·pyright·pytest)는 mock 러너로 결정론 검증.
실 LLM 품질은 여기서 CC가 라이브 파싱해 캡처 → Web는 매핑 타당성을 sanity-check(fixture↔eval 정합).

## 결과 요약 (7/7 정상 — 환각 0, 미등록 발명 0)

| # | 질의 | hard | soft | unsupported | 판정 |
|---|------|------|------|-------------|------|
| 1 | 강남 역세권 신축 어린이집 있는 큰 단지, 강아지 되면 좋고 | `has_daycare=true` | subway_time·approval_year·household_count, pet=preferred | `강남` | ✓ |
| 2 | 전용 84 이상, 주차 넉넉하고 세대수 많은 단지 | `net_area_min=84` | parking_ratio·household_count | — | ✓ |
| 3 | 지역난방에 CCTV 많고 승강기 충분한 곳만 | `heat_type="지역난방"` | cctv_count·elevator_count | — | ✓ |
| 4 | 전세 5억 이하 역세권 가까우면 좋고 | `deal_type=jeonse`·`deposit_max=50000` | subway_time | — | ✓ |
| 5 | 조용하고 바다 전망 좋은 신축 | — | approval_year | `조용하고`·`바다 전망 좋은` | ✓ |
| 6 | 어린이집 있는 곳만, 헬스장 되면 좋고 | `has_daycare=true` | gym=preferred | — | ✓ |
| 7 | 반려동물 가능한 큰 단지 선호 | — | pet=preferred·household_count | — | ✓ |

## 의뢰서 검증 매핑 대조 (Web sanity-check 기준)

- **역세권 → `subway_time`** (soft) — #1·#4 ✓
- **어린이집 → `has_daycare`** (hard, "있는 곳만") — #1·#6 ✓
- **큰 → `household_count`** (soft, 비교형) — #1·#2·#7 ✓
- **신축 → `approval_year`** (soft, "신축이면/신축" 비교) — #1·#5 ✓ (특정값 "신축 단지만"이면 hard `approval_year_min`로 갈 것)
- **강아지/반려동물 → `pet`** (soft preferred) — #1·#7 ✓
- **헬스장 → `gym`** (soft preferred) — #6 ✓

## 핵심 규율 확인

1. **레지스트리-grounded · 환각 0** — 7개 질의 전부 등록 key만 사용. 미등록 조건 발명 사례 없음.
2. **매핑 불가 = unsupported (발명 아님)** — "강남"(지역코드/좌표 매핑은 이 단계 밖)·"조용하고"·"바다 전망"이
   spec에 안 들어가고 `unsupported`로 표면화. 억지 hard 필터 박지 않음.
3. **hard vs soft 분류** — "~곳만/특정값"(어린이집·지역난방·전용 84·보증금 상한)은 hard, "넓은/많은/가까우면/
   되면 좋고"(주차·세대수·CCTV·승강기·역세권·gym/pet)는 soft. #3에서 "CCTV 많고/승강기 충분한"이 "곳만"의
   영향권 밖으로 올바르게 soft 유지.
4. **모호→soft (demote-not-exclude 보존)** — #5 "신축"은 hard `approval_year_min`이 아니라 soft approval_year로
   분류 → 후보 SET을 떨구지 않음(데이터 없는 단지도 중립 강등). #7 "반려동물 가능한"도 pet은 레지스트리상
   soft-only라 SET 불변.

## 주의 / 후속

- `deal_type`은 raw dump의 hard 표시에서 제외(기본값 sale 노이즈 억제)했으나 #4에서 `jeonse`로 정확히 설정됨
  (detected가 확증). 응답 spec에는 항상 실려 나간다.
- weight는 전부 기본 1.0 — 정밀 calibration·강/약/제외 튜닝은 #3 소관(범위 밖).
- "강남" 같은 지역명 → 지역코드/bbox 매핑은 별도 티켓(좌표 레이어). 현재는 unsupported로 정직하게 표면화.
