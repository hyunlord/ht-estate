# 의뢰서 T0-5 — 지오코딩 (소유 좌표 DB 오프라인 매칭)

## 목표
단지 도로명주소 → complex.lat/lng. 소유·저장 가능한 공개 좌표 DB로 오프라인 매칭
(키 없음, 영구 캐시). 실시간 지오코딩 API는 저장금지라 안 씀.

## 소스
- a-light(서울 즉시 다운) 좌표 없음 → **a1 행안부 위치정보요약DB**(EPSG:5179, 신청필요).
- 파이프라인은 문서포맷+fixture로 먼저 구현·게이트, 실 DB 투입은 deferral(설계 D).

## 범위
- IN: A. 좌표DB 로드+5179→WGS84 변환 / B. road_addr→좌표 매칭 / C. 백필(멱등) /
  D. 단계분리(심사 병행) / E. provenance(출처·기준일).
- OUT: hard filter(T0-6)·지도 UI(T0-7)·전국·실시간 지오코딩 API.

## 수용 기준 (DoD)
- [ ] 소스 결정+근거(a1, 신청 메모).
- [ ] 5179→WGS84 변환 검증(강남 lat≈37.5/lng≈127).
- [ ] road_addr 매칭: 정상→좌표, 무매치 graceful.
- [ ] 백필 멱등(lat 있으면 skip), provenance 기록.
- [ ] make gate green(키리스).
- [ ] (실 DB 시) 라이브 매칭률·좌표 타당성.

## 프로토콜
- §7 PLAN→DEBATE→CHALLENGE(소스·좌표계·파싱·무매치) + §8.2 풀 루브릭.
- 선행: PR #1~#5 미머지면 T0-4 위 스택. T0-4b와 병행.

## 산출물
- 브랜치 feat/T0-5-geocoding, PR. 리턴팩 + 소스결정 + 변환검증. 다음: T0-6 hard filter.
