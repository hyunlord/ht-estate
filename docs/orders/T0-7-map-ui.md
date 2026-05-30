# 의뢰서 T0-7 — Kakao 지도 + 필터 패널 + 단지 카드 (프론트 · Phase 0 마지막)

## 목표
Phase 0를 눈에 보이게: Kakao 지도 마커 + 필터 패널(HardFilterSpec 편집) + 단지 카드
(조건 ✓ + 추정매칭 배지 + 출처 딥링크). T0-6 API + T0-5b 좌표 결합.

## 경계
- gym 필터 없음(R1). NL→spec(LLM) OUT(구조화 폼만). soft/enrichment 표시 OUT(hard ✓만).

## 범위
- A. API 클라이언트(searchComplexes) / B. Kakao 지도(키 from env, bbox→search→마커,
  키 부재 graceful) / C. 필터 패널(HardFilterSpec 폼, gym 없음) / D. 단지 카드(조건 ✓·
  배지·source_url 딥링크·대표거래) / E. API CORS(localhost:3000) / F. 게이트(eslint·tsc·
  build·playwright shell 키리스).

## 수용 기준 (DoD)
- [ ] API 클라이언트 타입 일치. 지도 키 from env + 키부재 graceful. 필터 패널 gym 없음.
- [ ] 카드: 조건 ✓·배지·딥링크·대표거래. CORS. 게이트 키리스 green.
- [ ] (사람) 라이브 시각 확인: API+강남DB+JS키로 next dev → 마커+카드.

## 프로토콜
- §7 + §8.2 풀 루브릭. 키리스 게이트는 shell까지, 라이브 지도는 사람 시각.
- 베이스: feat/T0-6(#12).

## 산출물
- 브랜치 feat/T0-7-map-ui, PR. 리턴팩 + 스크린샷/라이브 노트. 다음: Phase 1 enrichment(강아지+gym 재배치).
