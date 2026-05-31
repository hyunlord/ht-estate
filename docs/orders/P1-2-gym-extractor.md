# 의뢰서 P1-2 — gym 추출기 (첫 실 enrichment 속성)

## 목표
P1-1 골격의 Extractor에 꽂는 첫 실 추출기: 단지 → 웹검색 + httpx fetch + LLM 추출
→ EnrichmentFact[] for gym. R1 정합(K-apt 미신뢰, 공식홈 우선, 단지내 vs 인근 상업
구별, no-signal=UNKNOWN). 의존성 주입형 → 키리스 게이트; 실 추출 R3 조건부.

## 범위
- A. GymExtractor(cid→단지→검색→fetch→LLM) / B. EnrichmentFact 조립(출처품질×LLM, no-signal→UNKNOWN) /
  C. IP/legal 가드(공개페이지만·본문재현금지·네이버/호갱노노/아실 미스크레이프) /
  D. 테스트(mock, 키리스) / E. 실 검증(R3 조건부).
- OUT: API 연결·카드 표시(P1-2b), 다른 속성(P1-3).

## 수용 기준 (DoD)
- [ ] GymExtractor가 P1-1 Extractor(cid,"gym") 만족, runner 주입.
- [ ] 파이프라인 + 단지내 vs 상업 구별 + confidence(출처×LLM) + no-signal→UNKNOWN.
- [ ] IP 가드(공개페이지·요지만·차단도메인).
- [ ] 테스트(mock search/fetch/llm): 구별·confidence·다출처·no-signal·fetch실패·runner결합.
- [ ] (사람/R3) 실 검증 또는 deferral 명시.

## 프로토콜
- §7 + §8.2 풀 루브릭. 실 추출 R3 조건부(키리스 게이트 완결 + 실 검증 후속).

## 산출물
- 브랜치 feat/P1-2-gym-extractor, PR. 리턴팩 + deferral 명시. 다음: P1-2b API/카드 또는 P1-3 강아지.
