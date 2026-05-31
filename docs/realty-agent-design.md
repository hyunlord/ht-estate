# 부동산 Agent — 전체 설계 (v1)

## 0. 한 줄 요약

조건으로 검색이 안 되는 정보(헬스장·전용면적·세대당 주차·신축 정도·강아지·구조)를, **공공 API로 벌크 구축한 결정론 레이어**와 **필요할 때만 긁어와 캐싱하는 확률 레이어**로 합쳐서, **모든 사실에 출처를 달아 보여주는** 단지 탐색 에이전트.

---

## 1. 핵심 원칙

**원칙 1 — 한 개의 저장소, 두 가지 적재 모드.**
"미리 구축"과 "그때그때 검색"은 별개 시스템이 아니라 같은 저장소의 cold/warm path다.
- **eager warm (build)**: 유한·저변동·고적중·벌크수신 가능한 데이터를 미리 전부 적재. → MOLIT 실거래 + K-apt 단지정보.
- **lazy read-through (search)**: cache miss(없음 or TTL 만료) 시 라이브로 가져와 추출하고, 결과를 TTL 걸어 다시 써넣는다(write-back). → 강아지·구조평가·후기.

**원칙 2 — lazy의 정당화는 신선도가 아니라 후보 수.**
hard filter 통과 단지는 한 줌(~20개)이다. 전국 수만 단지의 반려동물 정책을 미리 LLM으로 뽑을 이유가 없다. 비싼 추출은 살아남은 후보에만 낸다.

**원칙 3 — 모든 사실은 출처를 들고 다닌다.**
필드 단위로 `(값, 출처유형, source_url, fetched_at, confidence)`를 함께 저장한다. UI는 source_url을 딥링크로 노출(출처 이동)하고, 에이전트는 이 메타로 "언제 적 정보인지 / 믿어도 되는지"에 답한다.

---

## 2. 설계 결정 (기본값 — 변경 가능)

| 항목 | 기본값 | 승급 경로 |
|---|---|---|
| 범위 | 서울·매매 | 지역코드/거래유형만 추가 → 전국·전월세 |
| 저장소 | SQLite 단일 파일 | Postgres + PostGIS (멀티유저·지오쿼리) |
| 포지션 | 개인용 우선 | write-back 캐시라 멀티유저로 그대로 확장 |
| 네이버 | 코퍼스 제외 | 개인용 한정 수동 확인/아웃링크만 |
| 지오코딩 | Kakao Local (or VWorld) | — |

---

## 3. 시스템 구조

```
L0  데이터 소스
    ├─ 구조화(벌크)  : 공공데이터포털 MOLIT 아파트 매매 실거래 / K-apt 단지 기본정보 + 단지 목록 / Kakao 지오코딩
    └─ 비구조(온디맨드): 웹검색(블로그·뉴스·카페) / YouTube 자막 / 관리규약 / (개인용)호갱노노·아실

L1  Ingestion (eager)        : 단지 열거 → 단지정보 적재 → 실거래 적재 → 지오코딩 → 실거래↔단지 퍼지조인 → canonical store
L2  Canonical Store          : complex / transaction / enrichment (+ provenance)
L3  Retrieval & Agent        : NL→filter_spec → hard SQL(후보) → soft enrichment(lazy read-through, 병렬)
L4  Ranking & Explanation    : soft 점수로 후보 랭킹 + 조건별 ✓/△/✗ + 출처 링크
L5  Frontend                 : Kakao 지도(뷰포트 bbox·줌별 마커) + 필터 패널 + 단지 카드(근거+실거래 차트)
```

**쿼리 타임 데이터 흐름**
1. 사용자 자연어 → LLM → `filter_spec { hard:{...}, soft:{...} }`
2. hard 조건 → `complex ⨝ transaction` SQL (+지도면 bbox) → 후보 ~20
3. 후보 × soft 속성 → enrichment 조회 → miss/만료면 리서치 태스크 디스패치(병렬 3–5) → 추출 → write-back(TTL) → 반환
4. soft 속성 점수화 → 후보 랭킹
5. 단지 카드 렌더 (조건별 상태 + confidence + source_url 딥링크 + fetched_at)

---

## 4. 데이터 모델

```sql
-- 단지 (K-apt, eager)
complex (
  complex_id        TEXT PRIMARY KEY,   -- K-apt 단지코드
  name              TEXT,
  sido, sigungu, eupmyeon, dong         TEXT,
  legal_addr, road_addr                 TEXT,
  lat, lng          REAL,               -- 지오코딩 (정적, 1회)
  approval_date     DATE,               -- 사용승인일 → 신축 정도
  household_count   INTEGER,            -- 세대수
  building_type     TEXT,               -- 건물구조
  corridor_type     TEXT,               -- 복도유형 (계단식=판상형 확률↑)
  parking_total, parking_ground, parking_underground INTEGER,
  parking_ratio     REAL,               -- = parking_total / household_count (파생)
  amenities_raw     TEXT,               -- 부대복리시설 원본 텍스트
  has_gym           BOOLEAN,            -- 파생: K-apt 부대복리시설 키워드(약한 신호). ⚠️ R1: 헬스장이 거의 미기록(강남·송파·서초 0/17) → hard filter 부적합. gym은 Tier-2 enrichment 속성(§6). 이 컬럼은 보조 신호로만 보존.
  updated_at        TIMESTAMP,
  source_url        TEXT                -- K-apt 단지 페이지 (출처 이동)
)

-- 실거래 (MOLIT, eager)
transaction (
  txn_id            TEXT PRIMARY KEY,
  complex_id        TEXT REFERENCES complex,  -- 퍼지 매칭 (NULL 가능)
  match_confidence  REAL,                      -- 조인 신뢰도
  apt_name_raw      TEXT,                       -- MOLIT 아파트명 원본
  legal_dong, road_addr  TEXT,
  build_year        INTEGER,
  net_area          REAL,                       -- 전용면적
  price             INTEGER,
  floor             INTEGER,
  deal_date         DATE,
  updated_at        TIMESTAMP
)

-- enrichment: 출처를 들고 다니는 통합 사실 테이블 (lazy-filled)
enrichment (
  complex_id        TEXT REFERENCES complex,
  attribute         TEXT,        -- 'pet_allowed' | 'floorplan_eval' | 'review_summary' ...
  value             TEXT,        -- JSON
  confidence        REAL,        -- 0..1
  source_type       TEXT,        -- 'kapt'|'molit'|'web'|'youtube'|'cafe'|'blog'
  source_url        TEXT,        -- 딥링크 (출처 이동)
  fetched_at        TIMESTAMP,
  ttl_expires_at    TIMESTAMP,
  PRIMARY KEY (complex_id, attribute, source_url)   -- 한 속성에 여러 출처 보관
)
```

> `enrichment`이 `(단지, 속성)`당 여러 행(출처당 1행)을 갖는 게 핵심. 강아지 가능 여부에 대해 카페글·블로그·관리규약을 **모두 보관**해서, 에이전트가 종합·판단하고 UI는 출처를 전부 노출할 수 있다.

---

## 5. Tier 1 — Ingestion (eager warm)

**파이프라인**
1. **단지 열거**: K-apt 단지 목록 API → 단지코드 리스트 (시도/시군구 단위)
2. **단지정보 적재**: 단지코드별 K-apt 기본정보 → `amenities_raw`에서 `has_gym` 파싱(약한 보조 신호 — R1: 헬스장 거의 미기록이라 hard filter엔 안 씀, gym 판정은 Tier-2 §6), `parking_ratio = parking_total / household_count` 계산, 지하주차·사용승인일·복도유형 매핑
3. **실거래 적재**: MOLIT API(지역코드 × 계약월) → 전용면적·가격·층·건축년도·거래일
4. **지오코딩**: 단지 도로명주소 → lat/lng (정적이므로 1회 + 영구 캐시)
5. **조인**: 아래

### 5.1 조인 문제 (이 설계의 진짜 일)

MOLIT 실거래엔 **단지코드가 없다.** `아파트명 + 법정동 + 건축년도 + 도로명`만 있다. K-apt엔 `단지코드 + 단지명 + 법정동주소 + 도로명주소 + 사용승인일`이 있다. 둘을 매칭해야 단지 속성(헬스장·주차…)과 실거래(가격·전용면적)가 한 단지로 묶인다.

**매칭 전략 (강→약 순서, 통과 못 하면 다음으로)**
1. 정규화된 **도로명주소 일치** + (건축년도 ≈ 사용승인일 연도, ±1) → confidence 0.95
2. 법정동 + **단지명 퍼지 일치**(정규화 후 Levenshtein/Jaccard; "래미안OO" 차수·표기 흔들림 흡수) + 건축년도 근접 → confidence 0.7~0.85
3. 매칭 실패 → `complex_id = NULL`, 실거래는 보존하되 단지 속성 결합 불가로 표시

`match_confidence`를 transaction에 저장하고, 0.7 미만 매칭은 UI에서 "추정 매칭" 배지. 정규화 함수(괄호·차수·아파트/APT 접미사·공백 처리)는 별도 모듈로 빼서 테스트.

### 5.2 스케줄
- 실거래: **월 단위** 증분 (전월 데이터 확정 시점)
- 단지정보: **분기** (거의 정적)
- 지오코드: **1회**

---

## 6. Tier 2 — Enrichment (lazy read-through)

**트리거**: hard filter 후 살아남은 후보에 대해서만. soft 속성 조회 → `ttl_expires_at` 만료 또는 행 없음이면 라이브 적재.

**적재 절차 (속성당)**
1. 단지명/주소로 소스 검색: 웹검색(블로그·뉴스·카페), YouTube(단지명 → 영상 → 자막), 가능하면 관리규약
2. LLM 추출 → 구조화 값 + confidence + 근거 인용 + source_url
3. `enrichment`에 write-back, 속성별 TTL 설정
4. 후보 N개 병렬(3–5 동시 — 과도하면 봇차단/레이트리밋)

**속성별 처리**
- `pet_allowed` (강아지): **가장 약한 고리.** 단지가 아니라 관리규약 단위라 카페글 "된대요" 한 줄로 단정 금지. confidence 보수적, 출처 전부 노출, 값에 `"확인 권장: 관리사무소"` 플래그. 견종/무게 제한 같은 단서도 함께 저장.
- `floorplan_eval` (구조): **VLM으로 좋다/나쁘다 점수 매기지 말 것.** 평면도 이미지에서 **객관 feature만** 추출 — bay 수, 향(남향 여부), 판상/타워. "좋은 구조"는 사용자 가중치로 점수화. (VLM scoring calibration은 신뢰 못 함)
- `review_summary` (후기): YouTube 자막 + 블로그/카페 → 요약 + 출처. 재배포 아닌 개인 리서치 범위로 한정.
- `gym` (헬스장): **R1로 Tier-1에서 이동.** K-apt 부대복리시설에 헬스장이 거의 미기록(강남·송파·서초 0/17)이라 결정론 hard 필터 불가 → 웹/리뷰·단지 홈페이지에서 단지 내 피트니스 유무 추출. K-apt `has_gym`(약한 신호)은 보조 입력으로 참고하되 confidence는 출처 기반.

---

## 7. 쿼리 · 랭킹 · 설명

**NL → filter_spec**: LLM이 "헬스장 있고 너무 안 오래된 전용 84 강아지 되고 지하주차 세대당 널널한"을 구조화.
```json
{
  "hard": { "approval_year": [2010, 2022],
            "net_area": { "around": 84 }, "parking_underground": true,
            "parking_ratio": { "gte": 1.3 } },
  "soft": { "pet_allowed": "required", "floorplan": "good", "gym": "preferred" }
}
```
> ⚠️ R1 정정: `gym`은 **hard가 아니라 soft**다. K-apt 부대복리시설에 헬스장이 거의 기록되지 않아(0/17) 결정론 hard 필터로 못 쓴다 → Tier-2 enrichment(§6)로 웹/리뷰에서 추출해 soft 점수화.

- **hard** → `complex ⨝ transaction` SQL (지도면 bbox 추가)로 후보 산출 (이진 in/out)
- **soft** → 후보를 enrichment 점수로 랭킹. `required`인데 confidence 낮으면 강등하되 제외는 안 함(정보 부족 ≠ 부적합)

**설명/출처 (출처 보여주기 + 이동)** — 단지 카드 예:
```
래미안OO 3차
  사용승인 2015 ✓
  전용 84.97㎡  ✓  실거래 14.2억 (2025-04)          [실거래]
  지하주차      ✓  세대당 1.42대                     [K-apt]
  강아지        △  관리규약상 가능·견종 제한 / 확인 권장
                   ↳ 출처: 맘카페 글 (2024-03, conf 0.6) [이동]
  헬스장        △  단지 내 피트니스 (Tier-2 추출)      [이동]
                   ↳ 출처: 블로그 후기 (2025-01, conf 0.7) [이동]
  구조          ○  판상형·3bay·남향 (평면도 추출)     [평면도]
```
각 줄의 `[…]`는 source_url 딥링크.

---

## 8. 신선도(TTL) 정책

| 데이터 | 적재 | TTL/주기 |
|---|---|---|
| 실거래 | eager | 월 증분 |
| 단지정보 | eager | 분기 |
| 지오코드 | eager | 영구 |
| 강아지/구조 | lazy | 수 주~분기 (거의 안 변함) |
| 후기 | lazy | 수 주 |
| 매물(실시간) | **캐시 안 함** | 개인용 수동 확인 / 아웃링크 |

> 매물 단위 실시간("지금 누가 내놨나")은 네이버/직방 영역이라 코퍼스에 안 넣는다. 변하지 않는 단지정보 + 천천히 쌓이는 후기 + 빨리 변하는 매물을 분리하는 게 원칙.

---

## 9. 프론트엔드 (L5)

- **Kakao 지도**: 뷰포트 → bbox 쿼리 → 마커, 줌 레벨별 마커 클러스터링/집계 (Threads 프로젝트와 동일 패턴)
- **필터 패널**: filter_spec를 편집 가능한 폼으로 (NL 입력 + 수동 조정 둘 다)
- **단지 카드**: 조건별 ✓/△/✗ + confidence + 출처 딥링크 + 실거래 추이 차트
- 스택은 Next.js 등 자유 (v1은 단순해도 됨)

---

## 10. 빌드 순서 (Claude Code lead → Codex 디스패치)

워크트리: `wt/lead` + `wt/t-<id>-<slug>`. 리드가 티켓 생성·검증·머지, 구현은 `ask_codex`.

- **Phase 0 — Tier 1만으로 네이버 초월**
  T0-1 공공 API 클라이언트(MOLIT·K-apt) + SQLite 스키마 / T0-2 단지정보 파싱(has_gym·parking_ratio) / T0-3 실거래 적재 / T0-4 **퍼지 조인**(5.1, 정규화 모듈 + 테스트) / T0-5 지오코딩 / T0-6 hard filter API / T0-7 Kakao 지도 + 필터 패널.
  → 이 시점에 이미 "사용승인일+전용+지하주차+세대당주차+가격+지도" hard 필터가 됨(네이버 불가). (헬스장은 R1대로 Tier-1 제외 → Phase 1 Tier-2 enrichment 속성.)
- **Phase 1 — enrichment 골격 + 강아지**
  enrichment 테이블 + lazy read-through(웹검색→추출→write-back→TTL) + provenance UI(출처 링크).
- **Phase 2 — 구조 평가**
  평면도 수집 + VLM **feature 추출**(bay·향·판상/타워, 점수화 아님) → soft 랭킹 반영.
- **Phase 3 — 후기 + 랭킹/설명 완성**
  YouTube 자막 + 블로그/카페 요약(출처 포함) + 조건별 설명 카드.

---

## 11. 리스크 / 주의

1. **퍼지 조인이 Tier 1의 실제 난이도.** API 호출이 아니라 여기에 시간이 간다. match_confidence와 정규화 테스트가 품질을 좌우.
2. **네이버 제외 = 실시간 매물 공백.** 공공 API는 단지/실거래지 "지금 매물"이 아니다. 개인용은 수동 확인으로 메우고, 서비스화하면 데이터베이스권 판례(다윈중개) 사정권이므로 재설계 필요.
3. **강아지 confidence.** 관리규약 단위 + 출처 신뢰도 낮음 → 자문성으로 표기, 관리사무소 확인 안내. 잘못된 "가능"이 실제 피해로 이어질 수 있는 항목.
4. **VLM은 feature 추출만.** 주관 점수는 calibration 안 맞음.
5. **lazy 병렬도/레이트리밋.** 후보 3–5 동시 상한, 소스별 백오프.
