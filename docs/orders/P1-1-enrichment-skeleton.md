# 의뢰서 P1-1 — Enrichment 골격 (Tier-2 lazy read-through) + 설계 정정

## 목표
Tier-2 확률 레이어 재사용 머신: 후보×속성 → enrichment store(TTL) → miss면 주입형
추출기 → write-back(TTL+provenance), 병렬. 추출기 주입형이라 키리스 게이트. + gym Tier-1→Tier-2 설계 정정.

## 범위
- A. enrichment store(TTL read 다중출처 · write-back provenance 6필드 · 멱등) /
  B. lazy read-through 오케스트레이션(hit 캐시 / miss 추출+write-back, 병렬 3~5, 속성별 TTL) /
  C. Extractor 주입형 인터페이스 + stub / D. 설계 §4/§5/§7/§10 gym 정정 / E. 테스트(:memory:+fake).
- OUT: 실 추출기(P1-2)·구체 속성(강아지·gym)·카드 UI·search 연결(P1-2).

## 수용 기준 (DoD)
- [ ] store: TTL read(다중출처)·write-back(provenance 6)·멱등.
- [ ] runner: hit(추출기 미호출)/miss(추출+write-back), 병렬 상한, 속성별 TTL, {후보→facts}.
- [ ] Extractor 주입형 + stub. 설계 gym Tier-1→Tier-2 정정.
- [ ] 테스트(fake): hit/miss/다중출처/TTL만료/병렬/provenance. make gate 키리스 green.

## 프로토콜
- §7 + §8.2 풀 루브릭(≥95). fake 추출기·:memory: 결정론(deferral 없음). 베이스 main 또는 C3 위.

## 산출물
- 브랜치 feat/P1-1-enrichment-skeleton, PR. 리턴팩 + 설계 정정 diff. 다음: P1-2(첫 실 속성 추출기).
