# 의뢰서 T0-5b — 지오코딩 (실시간 geocoder + 캐시, 개인 단계)

## 배경
위치정보요약DB(소유·저장)는 신청·심사가 솔로 빌드엔 과해, 개인 단계는 실시간 geocoder
+ 캐시로 전환. **PR #8(오프라인 5179) supersede**. 심사 블로커 제거 → T0-5 풀 통과 가능.

## 소스
- (권장) Kakao Local 주소검색 — REST 키(T0-7 지도와 일원화), WGS84 직접.
- (대안) 국토부 지오코더(기존 data.go.kr 키).

## 범위
- IN: A. 응답 포맷 라이브 검증+실캡처 fixture / B. geocode 클라이언트(graceful) /
  C. 백필(멱등 영구캐시·throttle) / D. provenance / geo_repo 재사용, coord_db·convert·match 제거.
- OUT: hard filter(T0-6)·지도 UI(T0-7)·오프라인 좌표DB(서비스화 시 재검토).

## 수용 기준 (DoD)
- [ ] 소스 결정+포맷 라이브 확정+실캡처 fixture.
- [ ] geocode → (lat,lng), 무결과/에러 graceful.
- [ ] 백필 멱등(있으면 skip), throttle, provenance.
- [ ] make gate green(키리스).
- [ ] 라이브: 강남 N건 geocode → 좌표 타당성(deferral 아님, 풀 통과).

## 프로토콜
- §7 PLAN→DEBATE→CHALLENGE + §8.2 풀 루브릭(≥95). 라이브 검증 가능 → deferral 없음.
- 선행: PR #1~#7 머지 후 main 분기, PR #8 supersede.

## 산출물
- 브랜치 feat/T0-5b-geocoding, PR. 리턴팩 + 소스결정 + 라이브 좌표 타당성. 다음: T0-6 hard filter.
