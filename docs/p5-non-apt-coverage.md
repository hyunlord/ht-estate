# P5 — 비-아파트 커버리지 (연립·오피스텔·단독)

빌라(연립다세대)·오피스텔·단독을 매매/전월세와 함께 지도에 띄운다. **1안(실거래-derived)** 으로 건물
SET을 만들고, **2안(건축물대장 속성 backfill)** 이 *같은 키로 덧대지도록* 설계한다(교체 아님).

## 레이어
- **1안** = 건물 SET + 가격/전용/연식/층 (MOLIT 실거래에서). 얇은 속성.
- **2안** = 그 위에 구조·세대수·주차 등 (건축물대장) backfill. **덧댐, not replace.**

## property_type 축
`property_type ∈ {apartment, rowhouse(연립다세대), officetel, detached(단독)}`.
- `complex.property_type` 컬럼(P5-1a). 기존 K-apt 행 = `apartment`(init_db 백필 + `_complex_where`가
  레거시 NULL을 apartment로 취급 → 정합).
- 비-아파트 속성은 **얇음**: net_area·build_year·floor·price만(거래). 아파트 전용(has_gym·parking_*·
  household_count·subway_time 등)은 **NULL**(없는 게 정상 — 0/false 아님). 2안용 자리.

## ★ 건물 키 = 2안 조인 키 (정규화 도로명 주소 [+ 연식])
비-아파트는 K-apt 단지코드 같은 마스터 PK가 없다. 그래서 건물 식별자를 **결정론 정규화 키**로 잡는다:

```
building_key = normalize(road_addr) [+ build_year]   # app/match/normalize.normalize_name / 도로명 정규화 재사용(§5.1)
```

- 1안: 비-아파트 실거래의 도로명주소를 정규화 → 유니크 건물 → `complex` 행 생성(property_type 설정).
  거래는 그 건물에 조인(실거래-derived라 조인 단순 — 건물이 거래에서 나옴).
- **2안 조인 경로**: 건축물대장(세움터/공공데이터) 레코드의 도로명주소를 **같은 normalize로** 키화 →
  `building_key` 일치로 1안 건물에 구조·세대수·주차 backfill. **§5.1 정규화 모듈을 양쪽이 공유**하므로
  키가 동일 → 추가 매칭 로직 없이 조인.
- 정규화는 §5.1 `app/match/normalize.py`(괄호·동번호·구분자·접미사 제거) 재사용 — 드리프트 0.

## 검색/마커 포함 (자동)
- `repo._complex_where`에 property_type 필터(P5-1a). `search_complexes`·`search_markers`(#47)가 **둘 다
  _complex_where를 호출** → 비-아파트가 검색·마커에 자연 포함, property_type 필터도 양쪽 자동 적용.
- 아파트 전용 hard 조건(has_daycare 등)은 비-아파트엔 NULL → 안 매칭(정상). 거래유형/가격/전용/bbox는
  전 유형 공통.
- **NL 자동커버**: `criteria.REGISTRY`에 `property_type` criterion(enum 값 포함) 등록 → 파서 카탈로그에
  자동 주입 → "오피스텔"·"빌라" 질의가 파서 무수정으로 property_type에 매핑.

## 단계 (서브PR)
- **P5-1a (완료·이 PR, 키리스)**: property_type 스키마+백필 · spec/repo 필터 · 주택유형 criterion +
  NL 자동커버 · 검색 포함 · 테스트 · 아파트 회귀 0. (실 비-아파트 데이터 없음 — 머신만.)
- **P5-1b (다음, 키 보유 라이브)**: MOLIT 연립/오피스텔/단독 실거래 클라이언트(매매+전월세, `_http` 패턴
  재사용, 응답 필드명 라이브 검증) · 건물 도출(위 building_key) · geocode(기존 파이프라인) · cron stage
  (아파트와 동일 락/TTL/멱등 upsert) · 백그라운드 점진 적재. markers는 #47(map-first) 머지 후 자동 포함.

## 규율
- **2안-ready**: 건물 키 = 정규화 주소 — 건축물대장 backfill이 같은 키로 조인.
- **아파트 회귀 0**: 기존 아파트 마커/검색/조인 불변(property_type 미지정 시 동작 동일, 레거시 NULL=apartment).
- **멱등 적재**: 신규 실거래도 ON CONFLICT(txn_id) upsert.
- **NULL = 없는 것**, 0/false 아님.
