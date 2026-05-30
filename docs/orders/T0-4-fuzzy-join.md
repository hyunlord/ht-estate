# 의뢰서 T0-4 — 퍼지 조인 (transaction ↔ complex 매칭)

## 목표
각 실거래를 단지에 매칭해 transaction.complex_id + match_confidence를 채운다(설계 §5.1).
억지 매칭 금지 — 저신뢰/무매치는 NULL로 정직하게.

## 범위
- IN: A. aptSeq↔kaptCode 링크 라이브 검증 / B. 법정동 1차 필터 / C. 이름 정규화 /
  D. 유사도 매칭(임계+confidence) / E. 백필(멱등, 조인컬럼만 갱신).
- OUT: 지오코딩(T0-5)·hard filter(T0-6)·UI(T0-7)·Tier-2. 100% 매칭 강요.

## 수용 기준 (DoD)
- [ ] aptSeq↔kaptCode 링크 결론(리턴팩).
- [ ] 정규화: 괄호/차수/접미사 케이스 테스트.
- [ ] 매칭: 동일→high, 다름→NULL, 모호→NULL.
- [ ] 백필: NULL 거래 일괄, 멱등, 조인컬럼만 갱신.
- [ ] match_confidence 채워짐(매칭행), 무매치 NULL.
- [ ] make gate 키리스 green.
- [ ] (권장) 라이브 매칭률·오매칭 샘플.

## 프로토콜
- §7 PLAN→DEBATE→CHALLENGE(aptSeq·정규화범위·메트릭·모호처리·NULL정책) + §8.2 풀 루브릭.
- 선행: PR #1–#4 미머지면 T0-3 위 스택.

## 산출물
- 브랜치 feat/T0-4-fuzzy-join, PR. 리턴팩(§9) + aptSeq 결론 + 매칭률/오매칭 샘플. 다음: T0-5 지오코딩.
