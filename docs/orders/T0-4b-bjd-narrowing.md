# 의뢰서 T0-4b — 조인 recall 개선 ① bjd_code 결정론 narrowing

## 목표
T0-4 조인 narrowing을 동 *이름* → 법정동 *코드*(bjd_code) 동등으로 바꿔 동 표기변이 손실을
없앤다. 정밀도 유지(임계값 인하 금지). 지번 매칭은 후속 T0-4c.

## 범위
- IN: A. bjd_code 적재(complex.bjd_code·transaction.bjd_code = MOLIT sggCd+umdCd) /
  B. 결정론 narrowing(bjd_code 동등, 동 이름 fallback) / C. recall before/after + 오매칭 0.
- OUT: 지번 매칭(T0-4c)·hard filter·UI. 임계값 인하 금지. 전국 동별 인덱스.

## 수용 기준 (DoD)
- [ ] bjd_code 적재(멱등), MOLIT sggCd+umdCd = K-apt bjdCode 동등 확인.
- [ ] 백필 narrowing이 bjd_code 동등(동 표기변이 robustness 테스트).
- [ ] recall before/after + 오매칭 0(청담대림 여전히 NULL).
- [ ] 백필 멱등 + 조인컬럼만 갱신 유지.
- [ ] make gate green(키리스).

## 프로토콜
- §7 PLAN→DEBATE→CHALLENGE(bjd 동등 edge·narrowing 비파괴·정밀도 우선) + §8.2 풀 루브릭.
- 선행: PR #1~#5 미머지면 T0-4 위 스택.

## 산출물
- 브랜치 feat/T0-4b-bjd-narrowing, PR. 리턴팩(§9) + recall before/after + 오매칭 0. 다음: T0-4c 지번.
