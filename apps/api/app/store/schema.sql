-- ht-estate canonical store — 설계 §4 데이터 모델 1:1.
--
-- provenance 불변식(원칙3): 모든 사실은 (값, source_type, source_url, fetched_at,
-- confidence)를 들고 다닌다. 출처 컬럼을 제거/축소하지 말 것.
--
-- Postgres 승급 경계(원칙2): SQLite 전용 문법(AUTOINCREMENT·WITHOUT ROWID) 미사용,
-- 표준 타입만 사용 → Postgres 스왑 시 타입 맵핑만으로 이식 가능.
-- (BOOLEAN/TIMESTAMP/DATE는 SQLite에서 affinity로 흡수되고 Postgres에선 네이티브)
--
-- 적재(값 채우기)·derived 파싱(has_gym·parking_ratio)·조인은 T0-2+ 소관.
-- 이 티켓은 스키마 "생성"만 — provenance 컬럼이 전부 존재함을 introspection으로 증명.

-- 단지 (K-apt, eager)
CREATE TABLE IF NOT EXISTS complex (
  complex_id          TEXT PRIMARY KEY,   -- K-apt 단지코드
  name                TEXT,
  sido                TEXT,
  sigungu             TEXT,
  eupmyeon            TEXT,
  dong                TEXT,
  bjd_code            TEXT,               -- 법정동코드 10자리 (K-apt bjdCode) — 결정론 조인 narrowing
  legal_addr          TEXT,
  road_addr           TEXT,
  lat                 REAL,               -- 지오코딩 (정적, 1회) — T0-5
  lng                 REAL,
  approval_date       DATE,               -- 사용승인일 → 신축 정도
  household_count     INTEGER,            -- 세대수
  property_type       TEXT,               -- 주택유형 'apartment'|'rowhouse'(연립다세대)|'officetel'|'detached'(단독) — P5-1. NULL=apartment(기존 K-apt 백필)
  building_type       TEXT,               -- 건물구조
  corridor_type       TEXT,               -- 복도유형 (계단식=판상형 확률↑)
  parking_total       INTEGER,
  parking_ground      INTEGER,
  parking_underground INTEGER,
  parking_ratio       REAL,               -- = parking_total / household_count (파생) — T0-2
  amenities_raw       TEXT,               -- 부대복리시설 원본 텍스트
  has_gym             BOOLEAN,            -- 파생: 헬스/피트니스 키워드 파싱 — T0-2
  -- P4-1 풀필드 확장 (K-apt V4 basis+detail, additive·nullable). NL 토대. hard/soft 미연결(ticket #2).
  heat_type               TEXT,           -- 난방방식 (codeHeatNm)
  sale_type               TEXT,           -- 분양형태 (codeSaleNm)
  mgmt_type               TEXT,           -- 관리방식 (codeMgrNm)
  dong_count              INTEGER,        -- 동수 (kaptDongCnt)
  top_floor               INTEGER,        -- 최고층 (kaptTopFloor)
  priv_area               REAL,           -- 전용면적 합 ㎡ (privArea)
  mgmt_area               REAL,           -- 관리비부과면적 ㎡ (kaptMarea)
  builder                 TEXT,           -- 건설사 (kaptBcompany)
  developer               TEXT,           -- 시행사 (kaptAcompany)
  mgmt_staff              INTEGER,        -- 관리인원 (kaptMgrCnt)
  security_type           TEXT,           -- 경비방식 (codeSec)
  security_staff          INTEGER,        -- 경비인원 (kaptdScnt)
  cleaning_type           TEXT,           -- 청소방식 (codeClean)
  cleaning_staff          INTEGER,        -- 청소인원 (kaptdClcnt)
  disinfection_type       TEXT,           -- 소독방식 (codeDisinf)
  disinfection_staff      INTEGER,        -- 소독인원 (kaptdDcnt)
  disinfection_method     TEXT,           -- 소독방법 (disposalType)
  garbage_type            TEXT,           -- 음식물처리 (codeGarbage)
  water_supply            TEXT,           -- 급수방식 (codeWsupply)
  electricity_contract    TEXT,           -- 전기계약방식 (codeEcon)
  fire_alarm              TEXT,           -- 화재수신반방식 (codeFalarm)
  internet                TEXT,           -- 인터넷망 유/무 (codeNet)
  elevator_count          INTEGER,        -- 승강기 대수 (kaptdEcnt)
  cctv_count              INTEGER,        -- CCTV 대수 (kaptdCccnt)
  subway_line             TEXT,           -- 지하철 노선 (subwayLine)
  subway_station          TEXT,           -- 지하철 역명 (subwayStation)
  subway_time             TEXT,           -- 지하철 도보(역세권) (kaptdWtimesub)
  bus_time                TEXT,           -- 버스정류장 도보 (kaptdWtimebus)
  convenient_facility_raw TEXT,           -- 편의시설 원본 (convenientFacility)
  education_facility_raw  TEXT,           -- 교육시설 원본 (educationFacility)
  has_daycare             BOOLEAN,        -- 파생: 보육시설/어린이집
  has_playground          BOOLEAN,        -- 파생: 놀이터
  has_senior_center       BOOLEAN,        -- 파생: 노인정/경로당
  has_library             BOOLEAN,        -- 파생: 문고/도서관
  -- 건축물대장(enrich-1, BldRgstHubService 표제부/총괄표제부) — 비-아파트 빈 속성 벌크채움. additive.
  -- enrich-only(주소매칭된 기존 건물 속성만)·좌표 무접촉. 기존 컬럼(building_type·household_count·
  -- top_floor·dong_count·elevator_count·parking_total·approval_date)은 NULL일 때 대장으로 채움.
  main_purpose            TEXT,           -- 주용도 (mainPurpsCdNm) — 신규
  total_floor_area        REAL,           -- 연면적 ㎡ (totArea) — 신규
  ground_floor_count      INTEGER,        -- 지상 층수 (grndFlrCnt) — 신규
  basement_floor_count    INTEGER,        -- 지하 층수 (ugrndFlrCnt) — 신규
  building_coverage_ratio REAL,           -- 건폐율 % (bcRat) — 신규
  floor_area_ratio        REAL,           -- 용적률 % (vlRat) — 신규
  building_height         REAL,           -- 높이 m (heit) — 신규
  ho_count                INTEGER,        -- 호수 (hoCnt) — 신규
  -- provenance --
  updated_at          TIMESTAMP,
  source_url          TEXT,               -- K-apt 단지 페이지 (출처 이동)
  ledger_source_url   TEXT,               -- 건축물대장 출처(API 요청 식별) — enrich-1 provenance
  ledger_fetched_at   TIMESTAMP,          -- 대장 획득시각 — enrich-1 provenance
  ledger_pk           TEXT,               -- 대장 관리번호 (mgmBldrgstPk) — enrich-1 provenance
  ledger_bld_nm       TEXT,               -- 매칭된 대장 건물명 (bldNm) — 다중동 매칭 추적
  geo_source          TEXT,               -- 좌표 출처(DB명+기준일) — T0-5
  geo_updated_at      TIMESTAMP           -- 좌표 획득시각 — T0-5
);
-- 지도 bbox 핫패스 인덱스(complex-latlng-index) — search_markers/search_complexes의
-- `c.lat BETWEEN ? AND ? AND c.lng BETWEEN ?`가 172k 풀스캔하던 걸 lat 선행 레인지 스캔으로.
-- 매 뷰포트 pan/zoom + 콜드 첫 로드 지연 픽스. additive·메타데이터만(행/좌표 무변경 → 지문/counts 불변).
CREATE INDEX IF NOT EXISTS idx_complex_latlng ON complex(lat, lng);

-- 실거래 (MOLIT, eager)
CREATE TABLE IF NOT EXISTS "transaction" (
  txn_id              TEXT PRIMARY KEY,
  complex_id          TEXT REFERENCES complex(complex_id),  -- 퍼지 매칭 (NULL 가능) — T0-4
  match_confidence    REAL,                                 -- 조인 신뢰도 (지금 만들고 T0-4에서 채움)
  apt_name_raw        TEXT,                                 -- MOLIT 아파트명 원본
  legal_dong          TEXT,
  bjd_code            TEXT,                                 -- 법정동코드 = MOLIT sggCd+umdCd (= K-apt bjdCode) — 조인 narrowing
  jibun               TEXT,                                 -- 캐논 지번 "본번[-부번]" (MOLIT bonbun/bubun) — 지번 매칭 narrowing(T0-4c)
  road_addr           TEXT,
  build_year          INTEGER,
  net_area            REAL,                                 -- 전용면적
  price               INTEGER,                              -- 거래금액 (만원, MOLIT 원단위)
  floor               INTEGER,
  deal_date           DATE,
  -- provenance --
  updated_at          TIMESTAMP
);

-- 실거래↔단지 조인 조회용 인덱스 (T0-4·T0-6에서 쓰임, 지금 박아둠)
CREATE INDEX IF NOT EXISTS idx_transaction_complex ON "transaction"(complex_id);
-- initial-load-perf: 복합(complex_id, deal_date) — search_complexes의 최근일 MAX(deal_date) 정렬키를
-- 인덱스 시크로(7만 후보 상관 서브쿼리 가속). additive·메타데이터만(행/좌표 무변경 → 지문/counts 불변).
CREATE INDEX IF NOT EXISTS idx_txn_complex_date ON "transaction"(complex_id, deal_date);

-- 전월세 실거래 (MOLIT, 매매와 별도 데이터셋 — P2-1). 거래유형 축 확장(설계 §2).
-- 매매 transaction과 분리해 매매 회귀 0(별도 테이블). 조인 컬럼(complex_id·match_confidence·
-- apt_name_raw·legal_dong·bjd_code·jibun)은 transaction과 동형 → 퍼지조인(join_repo) 재사용.
-- 가격축만 다름: 매매=price / 전월세=deposit(보증금)+monthly_rent(월세, 전세=0).
CREATE TABLE IF NOT EXISTS rent_transaction (
  txn_id              TEXT PRIMARY KEY,
  complex_id          TEXT REFERENCES complex(complex_id),  -- 퍼지 매칭 (NULL 가능)
  match_confidence    REAL,
  apt_name_raw        TEXT,
  legal_dong          TEXT,                                 -- umdNm (법정동명 — 전월세는 코드 없이 이름만)
  sgg_cd              TEXT,                                 -- sggCd 5자리 — (sgg,동명)→bjd 룩업 키(P2-3)
  bjd_code            TEXT,                                 -- 법정동코드 10자리 — 전월세는 룩업으로 채움(P2-3)
  jibun               TEXT,                                 -- 캐논 지번 (지번 매칭)
  road_addr           TEXT,
  build_year          INTEGER,
  net_area            REAL,                                 -- 전용면적
  deposit             INTEGER,                              -- 보증금 (만원)
  monthly_rent        INTEGER,                              -- 월세 (만원, 전세=0)
  rent_type           TEXT,                                 -- 파생: 'jeonse'(월세 0) | 'monthly'
  contract_type       TEXT,                                 -- 계약구분 (신규|갱신, MOLIT contractType)
  floor               INTEGER,
  deal_date           DATE,
  -- provenance --
  updated_at          TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rent_transaction_complex ON rent_transaction(complex_id);
-- initial-load-perf: 전월세도 복합(complex_id, deal_date) — 최근일 정렬키 시크. additive·메타데이터만.
CREATE INDEX IF NOT EXISTS idx_rent_complex_date ON rent_transaction(complex_id, deal_date);

-- 적재 진행 원장 (C20) — 멀티데이 재개용. (stage, region, month) 완료분을 기록해 재개 시
-- 이미 fetch한 region×월을 일일캡 소모 없이 skip한다. 코어 적재(transaction/rent)와 별도 테이블이라
-- 회귀 0(additive). 0행 월도 기록 → 빈 월 재fetch 방지(데이터 추론으론 빈/미적재 구분 불가).
CREATE TABLE IF NOT EXISTS ingest_progress (
  stage       TEXT NOT NULL,            -- 'transaction' | 'rent'
  region      TEXT NOT NULL,            -- 시군구코드 (lawd_cd 5자리)
  month       TEXT NOT NULL,            -- YYYYMM (계약월)
  rows        INTEGER,                  -- 그 region×월에 적재된 행 수(0 가능)
  fetched_at  TIMESTAMP,
  PRIMARY KEY (stage, region, month)
);

-- enrichment: 출처를 들고 다니는 통합 사실 테이블 (lazy-filled, Phase 1+)
CREATE TABLE IF NOT EXISTS enrichment (
  complex_id          TEXT REFERENCES complex(complex_id),
  attribute           TEXT,        -- 'pet_allowed' | 'floorplan_eval' | 'review_summary' ...
  value               TEXT,        -- JSON
  confidence          REAL,        -- 0..1
  source_type         TEXT,        -- 'kapt'|'molit'|'web'|'youtube'|'cafe'|'blog'
  source_url          TEXT,        -- 딥링크 (출처 이동)
  fetched_at          TIMESTAMP,
  ttl_expires_at      TIMESTAMP,
  PRIMARY KEY (complex_id, attribute, source_url)   -- 한 속성에 여러 출처 보관
);

-- poi_proximity: 정적 좌표↔정적 POI 결정론 근접(eager Tier-1·poi-1). enrichment와 별개
-- (lazy/provenance/LLM 아님 — Kakao Local 거리계산). (단지,카테고리)당 1행. additive.
CREATE TABLE IF NOT EXISTS poi_proximity (
  complex_id      TEXT NOT NULL REFERENCES complex(complex_id),
  category        TEXT NOT NULL,        -- 'SW8'(지하철)|'MT1'(마트)|'CS2'(편의점)|'HP8'(병원)|'PM9'(약국)|'PARK'
  nearest_dist_m  INTEGER,              -- 최근접 POI 거리(m). 반경 내 0건이면 NULL.
  nearest_name    TEXT,                 -- 최근접 POI 이름(카드 표시)
  count_500m      INTEGER,              -- 500m 내 개수(반환 페이지 기준 — total>page면 하한)
  count_1km       INTEGER,              -- 1km 내 개수(Kakao meta.total_count)
  fetched_at      TIMESTAMP,
  source          TEXT,                 -- 'kakao_local'
  PRIMARY KEY (complex_id, category)
);
CREATE INDEX IF NOT EXISTS idx_poi_category ON poi_proximity(category, complex_id);

-- school_proximity: 정적 좌표↔정부 학교좌표 결정론 근접(eager Tier-1·school-1). poi_proximity와
-- 동형이나 소스/의미 다름(전국초중등학교위치표준데이터 15021148 거리 — Kakao 아님·외부 API 0).
-- (단지,level) 1행. additive. 좌표 read·이 테이블 write만 → 지문/counts 불변.
CREATE TABLE IF NOT EXISTS school_proximity (
  complex_id        TEXT NOT NULL REFERENCES complex(complex_id),
  level             TEXT NOT NULL,        -- 'elem'(초)|'mid'(중)|'high'(고)
  nearest_dist_m    INTEGER,              -- 최근접 학교 거리(m). 해당 level 학교 0개면 NULL.
  nearest_name      TEXT,                 -- 최근접 학교명(카드 표시)
  nearest_school_id TEXT,                 -- 최근접 학교ID(15021148)
  count_500m        INTEGER,              -- 500m 내 개수
  count_1km         INTEGER,              -- 1km 내 개수
  fetched_at        TIMESTAMP,
  source            TEXT,                 -- 'moe_school_location_15021148'
  PRIMARY KEY (complex_id, level)
);
CREATE INDEX IF NOT EXISTS idx_school_level ON school_proximity(level, complex_id);

-- school_assignment: 배정 초등 통학구역(school-2). 단지 좌표 point-in-polygon(초등 통학구역 SHP·
-- EPSG:5186) → 학구ID → 연계 CSV 조인 → 배정 초등. **초등 ONLY**(중/고는 평준화 추첨이라 배정 X).
-- 공동통학구역(zone_class='1')은 단지당 복수 학교 보관. 폴리곤 밖 단지는 sentinel 행(zone_id='')로
-- '계산했으나 배정 없음' 표시(resume done-set·read에서 제외=dash). 좌표 read·이 테이블 write만 →
-- 지문/counts 불변. additive. advisory(열람용·교육청 확인 — schoolzone 법적효력 없음).
CREATE TABLE IF NOT EXISTS school_assignment (
  complex_id   TEXT NOT NULL REFERENCES complex(complex_id),
  zone_id      TEXT NOT NULL,        -- 학구ID(HAKGUDO_ID). ''=sentinel(배정 없음·계산됨).
  zone_class   TEXT,                 -- 학구분류(HAKGUDO_GB) '0'=일반 '1'=공동통학구역
  school_id    TEXT,                 -- 배정 초등 학교ID(연계 CSV)
  school_name  TEXT,                 -- 배정 초등 학교명
  is_shared    BOOLEAN,              -- 공동통학구역(단지당 복수 배정)
  source       TEXT,                 -- 'schoolzone_elem_zone'
  source_url   TEXT,                 -- attribution(열람용·교육청 확인)
  fetched_at   TIMESTAMP,
  PRIMARY KEY (complex_id, zone_id, school_id)
);
CREATE INDEX IF NOT EXISTS idx_school_assignment_cid ON school_assignment(complex_id);

-- review_chunk: 후기/평판 RAG 코퍼스(E3-2). 후보-한정 lazy on-demand로 blog/cafe(차후 youtube)
-- 소스를 청킹해 적재 — chunk마다 출처(source_url)+인용정밀(span_ref: 문단idx/타임스탬프구간).
-- ★레시피핀(embed_model/dim/normalized)으로 모델변경 감지·재임베딩(geocode 지문 규율의 임베딩판).
-- TTL(후기 수주)로 신선도 관리. 벡터는 review_chunk_vec(sqlite-vec vec0 가상테이블·런타임 생성 —
-- 확장 로드 필요라 schema.sql 밖, app/corpus/vec.py가 chunk_id 조인으로 보강). additive·
-- review_chunk/_vec만 write(좌표/complex/transaction 무접촉) → 지문/counts 불변. DB권: 개인리서치
-- 범위·요약+출처(원문 대량 재배포 0).
CREATE TABLE IF NOT EXISTS review_chunk (
  chunk_id         TEXT PRIMARY KEY,     -- det. sha256(complex_id|source_url|span_ref) — 멱등 upsert
  complex_id       TEXT NOT NULL REFERENCES complex(complex_id),
  chunk_text       TEXT NOT NULL,        -- 청크 본문(요약 스니펫 — DB권 경계)
  source_type      TEXT NOT NULL,        -- 'blog'|'cafe'|'web'|'youtube'(차후)
  source_url       TEXT NOT NULL,        -- 딥링크(출처 이동)
  span_ref         TEXT,                 -- 인용정밀: 'p{idx}'(문단) | 't{start}-{end}'(자막구간)
  fetched_at       TIMESTAMP,
  ttl_expires_at   TIMESTAMP,            -- 만료 시 재수집(후기 신선도)
  embed_model      TEXT,                 -- ★레시피핀
  embed_dim        INTEGER,              -- ★레시피핀(1024)
  embed_normalized BOOLEAN               -- ★레시피핀
);
CREATE INDEX IF NOT EXISTS idx_review_chunk_complex ON review_chunk(complex_id);

-- pipeline_state: 적재기 자기서술 원장(pipeline-state). 각 파이프라인이 자기 상태(출생·목표·진행·
-- 마지막 실행·정상 여부)를 1행으로 기록 → 한 쿼리로 "얼마나 됐지/정상인지/언제 시작" 자기서술
-- (git 고고학·메모리 불요). **META 테이블 — 유일 write는 이 테이블 자신**(canonical 무접촉·read-only
-- COUNT/MIN으로만 진행 유도) → 지문/counts 불변. introduced_at은 write-once(출생일 보존·birth-vs-wipe
-- 혼동 차단). metric은 current/target가 무엇을 세는지 자가문서(rows-vs-distinct 모호 해소).
CREATE TABLE IF NOT EXISTS pipeline_state (
  name                 TEXT PRIMARY KEY,   -- 'poi_proximity'|'ledger_enrich'|'ingest_txn'|...
  introduced_at        TIMESTAMP,          -- ★최초 데이터 산출 시점(출생·write-once·provenance 유도)
  target_count         INTEGER,            -- 분모(예 172,879·POI는 지오코딩 수). 무경계면 NULL
  current_count        INTEGER,            -- metric 기준 진행
  metric               TEXT,               -- ★current/target가 세는 대상(예 "distinct complex_id with POI")
  last_run_at          TIMESTAMP,
  last_run_added       INTEGER,            -- 직전 기록 이후 증분
  status               TEXT,               -- 'filling'|'complete'|'on_demand'|'idle'|'error'
  expected_complete_at TIMESTAMP,          -- ETA(rate 산출 가능시·optional)
  updated_at           TIMESTAMP
);
