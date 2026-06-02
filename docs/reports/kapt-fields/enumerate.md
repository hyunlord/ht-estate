# K-apt V4 풀필드 enumerate — basis + detail (P4-1)

K-apt V4 두 엔드포인트 응답의 **전체 필드 목록**과, 이번 티켓에서 거둔 것/건너뛴 것을 매핑한다.

## 앵커 (검증 출처)
- 실응답 라이브 덤프는 **DATA_GO_KR 키 필요 → 사용자/CC ops**(§8.4 deferral). 본 리포트와 fixture는
  레포의 **실캡처 fixture**(`tests/fixtures/kapt_basis.json`·`kapt_detail.json` — 역삼자이 A10027474,
  T0-2 라이브 캡처)를 앵커로 한다. Web 검증은 **fixture↔이 리포트 정합 + plausibility**.
- 사용자 ops: 임의 단지로 `getAphusBassInfoV4`·`getAphusDtlInfoV4` 단건을 덤프해 이 목록과 대조(필드명
  드물게 흔들릴 수 있음 — 파서는 `_parse.json_*`로 graceful, 없는 필드는 None).

## getAphusBassInfoV4 (basis) — 응답 필드
| 필드 | 의미 | 처리 | 컬럼 |
|---|---|---|---|
| kaptCode | 단지코드 | 기존 | complex_id |
| kaptName | 단지명 | 기존 | name |
| kaptAddr | 지번주소 | 기존 | legal_addr |
| doroJuso | 도로명주소 | 기존 | road_addr |
| bjdCode | 법정동코드 | 기존 | bjd_code |
| kaptUsedate | 사용승인일 | 기존 | approval_date |
| kaptdaCnt | 세대수 | 기존 | household_count |
| codeHallNm | 복도유형 | 기존 | corridor_type |
| **codeHeatNm** | 난방방식 | **추가** | heat_type |
| **codeSaleNm** | 분양형태 | **추가** | sale_type |
| **codeMgrNm** | 관리방식 | **추가** | mgmt_type |
| **kaptDongCnt** | 동수 | **추가** | dong_count |
| **kaptTopFloor** | 최고층 | **추가** | top_floor |
| **privArea** | 전용면적 합 | **추가** | priv_area |
| **kaptMarea** | 관리비부과면적 | **추가** | mgmt_area |
| **kaptBcompany** | 건설사 | **추가** | builder |
| **kaptAcompany** | 시행사 | **추가** | developer |
| kaptTarea | 연면적 | skip | — (후보목록 외·NL가치 낮음) |
| codeAptNm | 단지분류(아파트…) | skip | — (후보목록 외) |
| hoCnt | 호수 | skip | — (세대수로 충분) |
| kaptMparea60/85/135/136 | 전용면적별 세대수 | skip | — (후보목록 외 — ticket #2 평형분포 시 검토) |
| kaptBaseFloor | 지하층수 | skip | — (후보목록 외) |
| ktownFlrNo | (지상최고층?) | skip | — (의미 모호 — 오매핑 방지) |
| kaptdEcntp | (승강기?) | skip | — (detail kaptdEcnt와 중복·모호 → detail 채택) |
| zipcode | 우편번호 | skip | — (geo niche) |
| kaptTel·kaptFax·kaptUrl | 연락처 | skip | — (NL 조건 무관) |

## getAphusDtlInfoV4 (detail) — 응답 필드
| 필드 | 의미 | 처리 | 컬럼 |
|---|---|---|---|
| codeStr | 건물구조 | 기존 | building_type |
| kaptdPcnt / kaptdPcntu | 지상/지하주차 | 기존 | parking_ground / _underground |
| welfareFacility | 부대복리시설 | 기존 | amenities_raw (+welfare 파생) |
| **kaptMgrCnt** | 관리인원 | **추가** | mgmt_staff |
| **codeSec / kaptdScnt** | 경비 방식/인원 | **추가** | security_type / security_staff |
| **codeClean / kaptdClcnt** | 청소 방식/인원 | **추가** | cleaning_type / cleaning_staff |
| **codeDisinf / kaptdDcnt / disposalType** | 소독 방식/인원/방법 | **추가** | disinfection_type / _staff / _method |
| **codeGarbage** | 음식물처리 | **추가** | garbage_type |
| **codeWsupply** | 급수방식 | **추가** | water_supply |
| **codeEcon** | 전기계약방식 | **추가** | electricity_contract |
| **codeFalarm** | 화재수신반방식 | **추가** | fire_alarm |
| **codeNet** | 인터넷망 | **추가** | internet |
| **kaptdEcnt** | 승강기 대수 | **추가** | elevator_count |
| **kaptdCccnt** | CCTV 대수 | **추가** | cctv_count |
| **subwayLine / subwayStation / kaptdWtimesub** | 지하철 노선/역명/도보(역세권) | **추가** | subway_line / _station / _time |
| **kaptdWtimebus** | 버스정류장 도보 | **추가** | bus_time |
| **convenientFacility** | 편의시설(raw) | **추가** | convenient_facility_raw |
| **educationFacility** | 교육시설(raw) | **추가** | education_facility_raw |
| codeMgr | 관리방식(detail) | skip | — (basis codeMgrNm로 채택, 중복) |
| codeEmgr | 전기안전관리자 | skip | — (후보 "전기 방식"=codeEcon만) |
| codeElev | 승강기 관리형태 | skip | — (후보 "승강기 수"=kaptdEcnt만) |
| kaptCcompany / kaptdSecCom | 관리/경비 회사명 | skip | — (후보 "방식+인원"만, 회사명 제외) |
| kaptdEcapa | 수전용량 | skip | — (niche) |
| groundElChargerCnt / undergroundElChargerCnt | 전기차충전기 | skip | — (후보목록·문서 미언급 — 다음 티켓 후보) |
| useYn | 사용여부 | skip | — (운영 플래그) |

## welfare 토큰 파생 (선택·라이트 — 명확 패턴만, 과파생 금지 원칙4)
`amenities_raw`(welfareFacility)에서 **명확 토큰만** boolean 파생. 모호하면 raw만 보존.
- `has_daycare` ← 보육시설·어린이집 · `has_playground` ← 놀이터
- `has_senior_center` ← 노인정·경로당 · `has_library` ← 문고·도서관
- **CCTV는 토큰 대신 구조화 `cctv_count`로 직접 거둠**(>0 판정은 ticket #2). EV충전기 count는 이번 skip.

## 요약
- **추가 raw 컬럼: 30** (basis 9 + detail 21) · **파생 boolean: 4** = complex에 34 nullable 컬럼.
- 마이그레이션 **additive·idempotent**(`init_db`가 빠진 컬럼만 ALTER ADD COLUMN nullable) — 실행 중
  적재 루프 디스럽트 없이 backfill-ready. 기존 컬럼/인덱스/파생(has_gym·parking_ratio)·resume 불변.
- hard/soft·NL 연결은 **ticket #2** (이번은 적재만). 랭킹 불변(SoftSpec=={gym,pet}).
