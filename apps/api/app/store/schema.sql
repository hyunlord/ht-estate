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
  building_type       TEXT,               -- 건물구조
  corridor_type       TEXT,               -- 복도유형 (계단식=판상형 확률↑)
  parking_total       INTEGER,
  parking_ground      INTEGER,
  parking_underground INTEGER,
  parking_ratio       REAL,               -- = parking_total / household_count (파생) — T0-2
  amenities_raw       TEXT,               -- 부대복리시설 원본 텍스트
  has_gym             BOOLEAN,            -- 파생: 헬스/피트니스 키워드 파싱 — T0-2
  -- provenance --
  updated_at          TIMESTAMP,
  source_url          TEXT,               -- K-apt 단지 페이지 (출처 이동)
  geo_source          TEXT,               -- 좌표 출처(DB명+기준일) — T0-5
  geo_updated_at      TIMESTAMP           -- 좌표 획득시각 — T0-5
);

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

-- 전월세 실거래 (MOLIT, 매매와 별도 데이터셋 — P2-1). 거래유형 축 확장(설계 §2).
-- 매매 transaction과 분리해 매매 회귀 0(별도 테이블). 조인 컬럼(complex_id·match_confidence·
-- apt_name_raw·legal_dong·bjd_code·jibun)은 transaction과 동형 → 퍼지조인(join_repo) 재사용.
-- 가격축만 다름: 매매=price / 전월세=deposit(보증금)+monthly_rent(월세, 전세=0).
CREATE TABLE IF NOT EXISTS rent_transaction (
  txn_id              TEXT PRIMARY KEY,
  complex_id          TEXT REFERENCES complex(complex_id),  -- 퍼지 매칭 (NULL 가능)
  match_confidence    REAL,
  apt_name_raw        TEXT,
  legal_dong          TEXT,
  bjd_code            TEXT,                                 -- sggCd+umdCd (조인 narrowing)
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
