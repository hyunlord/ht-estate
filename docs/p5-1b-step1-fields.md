# P5-1b STEP 1 — MOLIT 비-아파트 실거래 필드 라이브 검증 (deferral 해소 증거)

라이브 호출(키 보유): 1613000 RTMS, LAWD_CD=11680(강남), DEAL_YMD=202504, numOfRows=1. 2026-06-04.
**추정 금지** — 아래는 *실제 응답 태그*. 아파트(aptNm·roadNm·excluUseAr…)와 **다름**, 유형별로도 다름.

## ✅ 전월세 (인증됨 · 200 · 필드 확정)

| 유형 | 엔드포인트(200) | 건물명 | 면적 | 주소 단서 | 가격축 | 비고 |
|---|---|---|---|---|---|---|
| 연립다세대 RH | `RTMSDataSvcRHRent/getRTMSDataSvcRHRent` | **mhouseNm** | excluUseAr(전용) | jibun · umdNm · sggCd | deposit · monthlyRent (+ preDeposit/preMonthlyRent) | houseType, floor, buildYear, contractTerm/Type, useRRRight |
| 오피스텔 Offi | `RTMSDataSvcOffiRent/getRTMSDataSvcOffiRent` | **offiNm** | excluUseAr | jibun · umdNm · sggCd · sggNm | deposit · monthlyRent (+ pre*) | floor, buildYear, contractTerm/Type, useRRRight |
| 단독/다가구 SH | `RTMSDataSvcSHRent/getRTMSDataSvcSHRent` | **(없음)** | **totalFloorAr(연면적)** | umdNm · sggCd (**jibun 없음**) | deposit · monthlyRent (+ pre*) | houseType, buildYear, contract*, useRRRight. floor/excluUseAr/name 없음 |

전체 태그(원문):
- **RH전월세**: buildYear, contractTerm, contractType, dealDay, dealMonth, dealYear, deposit, excluUseAr, floor, houseType, jibun, mhouseNm, monthlyRent, preDeposit, preMonthlyRent, sggCd, umdNm, useRRRight (total=650)
- **Offi전월세**: buildYear, contractTerm, contractType, dealDay, dealMonth, dealYear, deposit, excluUseAr, floor, jibun, monthlyRent, offiNm, preDeposit, preMonthlyRent, sggCd, sggNm, umdNm, useRRRight (total=341)
- **SH전월세**: buildYear, contractTerm, contractType, dealDay, dealMonth, dealYear, deposit, houseType, monthlyRent, preDeposit, preMonthlyRent, sggCd, totalFloorAr, umdNm, useRRRight (total=568)

## ⛔ 매매 (미인증 · 403 Forbidden)
- `RTMSDataSvcRHTrade` · `RTMSDataSvcOffiTrade` · `RTMSDataSvcSHTrade` → **HTTP 403 Forbidden** (경로는 존재; `…TradeDev` 변형은 500=경로 없음).
- = 이 서비스키가 **연립/오피스텔/단독 *매매* 실거래 서비스에 활용신청 승인 안 됨**(전월세는 승인됨). → **외부 선결조건(사용자/data.go.kr 액션)** — CC 불가. 매매 필드는 승인 후 재검증.

## 🔑 발견된 설계 결정 (코딩 전 사용자/Web 비준 필요)
1. **building_key 재정의 필요** — 비-아파트 전월세 응답엔 **roadNm 없음**(jibun·umdNm·sggCd만). #48 문서의 "정규화 *도로명* 주소" 키는 비-아파트 전월세에 **불가**. 대안(권장): **PNU식 키 = `bjd_code(sgg+umd) + jibun [+ 정규화 건물명]`**. 이게 오히려 건축물대장(2안) 표준 키(PNU)와 정합 → 2안-ready 더 강함. (도로명은 geocode 후 역으로 확보.)
2. **단독(SH) 지도 부적합** — name·jibun·floor 없음, umdNm(법정동)+연면적뿐 → **개별 건물·좌표 도출 불가**(geocode할 주소 단서 부족). → SH는 지도 마커에서 **제외/보류** 권고(연립·오피스텔만 1안 대상).
3. **면적 의미차** — SH는 excluUseAr 없이 **totalFloorAr(연면적)** → 전용면적과 다른 축(혼동 금지). RH/Offi는 excluUseAr(전용) — 아파트와 동일.

## 권고 (다음)
- **즉시 가능(인증됨)**: RH·Offi **전월세** 클라이언트(검증 필드) + PNU식 building_key 도출 + geocode(jibun 주소) — **단, building_key 재정의(#1)·SH 제외(#2)를 비준받은 뒤** 코딩.
- **사용자 액션**: 연립/오피스텔/단독 **매매** 활용신청 승인 → 승인 후 매매 필드 재검증 → 매매 클라이언트.
- **타이밍**: 헤비 적재는 P0(K-apt 풀필드 refill, 진행 중)와 시간 분리.
