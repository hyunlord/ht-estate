# 의뢰서 C4 — gym 시드 부트스트랩 (에이전트 추출, (a) 경로)

## 목표
앱 런타임 키 없이 CC가 직접 강남 단지 gym을 에이전트로 추출해 enrichment에 정적 시드.
R1 규율(단지내 vs 상업 구별·no-scrape·요지만·보수적 confidence·provenance). 멱등·재개.

## 분류
데이터 부트스트랩 chore. 산출물=시드(품질 검증)+로더. R1 규율이 채점 기준.

## 작업
fresh gym fact 없는 각 강남 단지: 웹검색 → 공개페이지(공식홈·언론 우선, 네이버/호갱노노/
아실 미스크레이프) → 단지내 vs 인근 상업 구별(단정 못하면 UNKNOWN) → 출처별 1 레코드.
- 시드 data/seeds/gym_gangnam.jsonl(누적). 로더 scripts/load_gym_seed.py(write_facts 멱등·재개).

## 수용 기준 (DoD)
- [ ] 시드: 단지별 gym(has_gym·evidence_요지·confidence·source_type·실 source_url), 원문복사 없음.
- [ ] R1 규율: 단지내 vs 상업(예시 포함)·no-signal→UNKNOWN·차단도메인 출처 없음.
- [ ] 로더: write_facts 멱등·fresh skip(재개)·샘플 테스트.
- [ ] 리포트: 커버 N·분포·샘플 fact(출처)·규율·남은 수.
- [ ] make gate green(키리스).

## 산출물
- 브랜치 chore/C4-gym-seed, PR. 리턴팩 + 시드·분포·리포트·남은 수. 다음: C4 후속 배치 or P1-2b.
