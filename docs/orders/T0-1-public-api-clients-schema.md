# 의뢰서 T0-1 — 공공 API 클라이언트(MOLIT·K-apt) + SQLite 스키마

## 목표
Tier 1 적재의 입력단: MOLIT 아파트 매매 실거래 + K-apt(단지 목록·기본정보) API 클라이언트와,
설계 §4 canonical 스키마(`complex`·`transaction`·`enrichment`, provenance 포함)를 SQLite에 정의.
**클라이언트는 타입드 레코드 반환**, **스키마는 생성**까지 — 실제 적재·derived 파싱·조인은 이 티켓 밖.

## 범위
- IN:
  - MOLIT 실거래 클라이언트: `fetch_trades(lawd_cd, deal_ym) -> list[Trade]` (전용면적·거래금액·층·건축년도·거래일·아파트명·법정동·도로명). 페이지네이션·빈응답·에러 처리.
  - K-apt 클라이언트: `list_complexes(sido?, sigungu?) -> list[ComplexRef]` + `fetch_complex_info(kapt_code) -> ComplexInfo`(사용승인일·세대수·총/지상/지하주차·복도유형·건물구조·부대복리시설(raw)·도로명주소 …).
  - SQLite 스키마: `complex`·`transaction`·`enrichment` (설계 §4 그대로, provenance 포함). `init_db()` 생성.
  - API 키: env `DATA_GO_KR_API_KEY` 로드, 미설정 시 명확한 에러. `.env.example` 문서화.
  - 테스트: 파서는 캡처 샘플 fixture(라이브 금지), 스키마는 introspection.
- OUT: 실제 적재·derived 파싱(has_gym·parking_ratio)·지오코딩·퍼지 조인·hard filter·UI. 라이브 API 호출 금지.

## 수용 기준 (DoD)
- [ ] MOLIT 파서: fixture → 타입드 Trade, 필수필드 채워짐, 빈/에러 graceful.
- [ ] K-apt 목록/기본정보 파서: fixture → 필수필드.
- [ ] `init_db()`가 3테이블 생성, enrichment PK=(complex_id, attribute, source_url), provenance 컬럼 전부 존재(introspection).
- [ ] API 키 env 로드 + 미설정 에러, `.env.example` 문서화. 테스트는 라이브 키 불필요.
- [ ] 파서 테스트에 happy + empty/malformed.
- [ ] `make gate` 전체 green (web 무변경, 회귀 없음).

## 검증 / 프로토콜
- 객관 게이트: gate-api(ruff·pyright·pytest) + `make gate` 전체 green. 화면 검증 N.
- §7 PLAN→DEBATE→CHALLENGE + §8.2 풀 루브릭 self-verify(≥95 + 하한).
- Web 독립 검증: clone 후 `make gate-api` 재실행 + PR diff(web 무변경) 대조.

## 산출물
- 브랜치 `feat/T0-1-public-api-clients-schema`, PR. 리턴팩(§9).
- 다음 제안: T0-2(단지정보 파싱 — has_gym·parking_ratio).
