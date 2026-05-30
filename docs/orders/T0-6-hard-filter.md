# 의뢰서 T0-6 — Hard Filter API (complex ⨝ transaction)

## 목표
구조화 hard filter_spec → complex ⨝ transaction SQL → 후보 단지(이진 in/out). Phase 0 결실.

## 핵심 정정 (R1)
gym은 hard filter에서 제외(K-apt 헬스장 데이터 없음 0/17 → Tier-2). has_gym 필터 금지 + 부재 단언 테스트.

## 범위
- IN: A. HardFilterSpec(Pydantic, gym 없음) / B. search_complexes(complex속성+bbox+txn EXISTS,
  대표거래·거래수·가격range·match_confidence·source_url 동봉, 이진 in/out, limit) /
  C. FastAPI POST /complexes/search / D. :memory: 테스트.
- OUT: NL→spec(LLM)·soft/enrichment·점수랭킹·프론트(T0-7).

## 수용 기준 (DoD)
- [ ] HardFilterSpec 필드+범위정합+bbox, gym 없음.
- [ ] search_complexes: 필터+조인+대표거래+confidence+이진+limit.
- [ ] 각 차원·조인·저신뢰·빈결과·limit 테스트 + gym 부재 단언 + 라우트.
- [ ] make gate 키리스 green.

## 프로토콜
- §7 + §8.2 풀 루브릭. :memory: 결정론(deferral 없음).
- 베이스: 통합 main(미머지면 스택).

## 산출물
- 브랜치 feat/T0-6-hard-filter, PR. 리턴팩 + 필터 차원 예시 + gym 부재 확인. 다음: T0-7 지도.
