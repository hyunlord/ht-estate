# 의뢰서 T0-3 — 실거래 적재 (transaction rows)

## 목표
MOLIT 실거래(Trade)를 `transaction` 행으로 적재: 결정론 `txn_id` 생성 → 멱등 upsert →
지역×월 증분. 퍼지 조인(complex_id·match_confidence)은 T0-4 소관이라 NULL로 둔다.

## 범위
- IN: txn_id 결정론 해시 / upsert_transaction(provenance updated_at, complex_id·conf NULL, 멱등) /
  ingest_month(전 페이지→upsert, 빈·에러 graceful) / 기본 throttle(다월 루프 지연).
- OUT: 퍼지 조인·match_confidence(T0-4), 지오코딩(T0-5), hard filter(T0-6), UI(T0-7), 전국 풀 적재.

## 수용 기준 (DoD)
- [ ] txn_id 결정론(같은 Trade→같은 id, 다른 거래→다른 id).
- [ ] upsert_transaction: 필드·provenance 채움, complex_id/match_confidence NULL, 멱등. :memory: 테스트.
- [ ] ingest_month: fixture로 전 페이지→upsert(키 불필요), 빈/에러 graceful.
- [ ] throttle 존재(다월 루프), 결정론 단위 검증.
- [ ] make gate 전체 green(키리스).
- [ ] (권장) 라이브 E2E 1회.

## 프로토콜
- §7 PLAN→DEBATE→CHALLENGE(txn_id 필드·충돌처리·throttle·재적재 정책) + §8.2 풀 루브릭.
- 선행: PR #1·#2·#3 미머지면 T0-2 위에 스택.

## 산출물
- 브랜치 feat/T0-3-transaction-ingest, PR. 리턴팩(§9). 다음: T0-4 퍼지 조인.
