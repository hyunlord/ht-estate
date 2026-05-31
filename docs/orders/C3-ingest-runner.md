# 의뢰서 C3 — 적재 오케스트레이션 러너 (ingest CLI)

## 목표
전체 적재 파이프라인을 단일 명령으로: ingest <지역><월> → 단지→실거래→조인→지오코딩 순서.

## 분류
오케스트레이션+CLI+테스트 → [S] 아님. §7 + §8.2 풀 루브릭.

## 범위
- A. CLI(--region·--months·--db·--stages, .env 키, --help) / B. 4단계 순서 오케스트레이션
  (순서 강제, 부분선택 경고) / C. throttle·멱등 재개·진행 로깅·종료 요약.
- OUT: 새 적재 로직(기존 재사용), 스케줄러/cron, 멀티지역 병렬.

## 수용 기준 (DoD)
- [ ] CLI 인자·키리스 join·--help. 4단계 canonical 순서+선행 경고.
- [ ] 단지 region 루프(ingest_complexes) wire. throttle·멱등·로깅·요약.
- [ ] 오케스트레이션 테스트(스테이지 mock 키리스): 순서·부분선택·요약. make gate green.
- [ ] (사람) 라이브: ingest 11680 → 강남 적재.

## 프로토콜
- §7 + §8.2 풀 루브릭(≥95). 라이브는 키 보유(deferral 아님). 베이스 main(#14 후) 또는 C2 위.

## 산출물
- 브랜치 chore/C3-ingest-runner, PR. 리턴팩 + 라이브 적재 결과. 다음: Phase 1.
