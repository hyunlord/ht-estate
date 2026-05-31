# 리턴팩 C7 — 강남 커버리지 확대 (gym+pet 후속 시드)

## 변경 요약
강남3구를 풀 적재해 **실 kaptCode** 풀을 확보(C5의 10단지 병목 해소)하고, gym/pet 시드를 (a) 경로로
확대. gym은 C4 규율(단지 내 vs 상업·no-signal→unknown·오귀속 배제·보수적), pet은 C5 규율(보수적·
conditional+caveats·confirm 전수·약한 출처 yes 금지). 멱등·재개. Anthropic 키 불필요(CC 추출).

## 적재 (precondition — 실 kaptCode 공급)
`app.ingest --region <코드> --stages complex` (멱등):
- 강남구(11680): **232 단지** · 서초구(11650): **199** · 송파구(11710): **178** → DB **609 단지**(kaptCode 포함).
- pet 시드가 강남3구(서초·송파 포함) 코드를 참조하므로 3구 모두 적재(FK 충족). 거래·지오코딩은 시드와
  무관해 이번엔 complex 스테이지만(후속 가능).

## gym 시드 (14 단지 = C4 4 + C7 10 신규)
**분포: yes 8 · no 1 · unknown 5.** 신규 10: yes 6 · unknown 4.
- **yes(출처 확인)**: 도곡렉슬(KB부동산 — 피트니스 401동 B1, 0.7) · 개포래미안포레스트(입주공고 — 수영장·실내체육관, 0.6) · 디에이치자이개포(공식 THE H — 테크노짐, 0.85) · 래미안블레스티지(언론 — 호텔신라 피트니스, 0.8) · 래미안대치팰리스(SPOWISE CIMS 커뮤니티 관리시스템, 0.5) · 디에이치아너힐즈(공식 THE H+매경 — 스포츠존, 0.85).
- **unknown(규율)**: 개포상록스타힐스(신축이나 단지 특정 출처 미확인 — 타단지 노이즈) · 압구정현대(1976 재건축 대기 — 헬스장 언급은 재건축 후 청사진) · 대치미도(1983) · 압구정신현대(1982). **no-signal→unknown** 준수(구축이라도 단정 안 함).

## pet 시드 (15 단지 = C5 10 + C7 5 신규)
**분포: conditional 1(개포자이, C5) · unknown 14.** 신규 5: **전부 unknown.**
- 도곡렉슬·개포래미안포레스트·래미안블레스티지·디에이치자이개포·압구정현대 — 공개 per-complex 반려동물 정책 신호 없음(검색 0~노이즈). §11 "가장 약한 고리"가 더 큰 표본에서도 재확인됨 — 공개 출처 희소.
- **오귀속 배제**: '외부인 출입 제한(담장)' 보도는 사람 대상이라 반려동물 정책에 귀속하지 않음(evidence 명시).
- 중복 제거: 디에이치아너힐즈는 C5에 이미 있어 C7 pet 재추가분 제거(append-new-only).

## 규율 / 품질 (채점 기준)
- gym: 차단도메인(naver/hogangnono/asil) 출처 **0** · 단지 내 vs 상업 구별 · no-signal→unknown · 보수적 conf.
- pet: 차단도메인 **0** · `confirm_with_office` **15/15 true** · 약한 출처(cafe/blog) 단독 yes **0** · caveats 보존(개포자이).
- (complex_id, source_url) 중복 0 · complex_id 중복 0.

## 객관 게이트 (raw)
- ruff `All checks passed!` · pyright `0 errors` · pytest `190 passed`(시드 파싱·규율 테스트 포함, 회귀 0) · e2e `4 passed`.
- 로더 멱등: gym `0 적재·14 skip` · pet `0 적재·15 skip` 재실행 확인.
- `make gate` green.

## 미해결 / 결정필요
- **커버리지**: gym 14 / pet 15 단지(강남3구 609 중). 큰 폭 확대 여지 — 다음 세션이 누적(재개 가능).
- **pet unknown 편중(14/15)**: 공개 출처 한계의 구조적 결과. 라이브 추출(P1-2-live/로컬모델)이나 관리규약 직접 입수 없이는 개선 한계.
- **로더 CLI 사전 이슈(비차단)**: `uv run python scripts/load_*.py`가 `app` 미임포트(apps/api 미경로)로 실패 — `PYTHONPATH=. uv run python scripts/load_*.py`로 동작(C4/C5부터 존재). 게이트·테스트는 영향 없음(테스트는 sys.path 주입). 후속에서 부트스트랩/Makefile 타겟 권장.

## 다음 제안
- **C7 후속 배치** — 더 많은 강남3구 단지 gym/pet 추출(누적). 거래·지오코딩 스테이지 적재.
- **P1-2-live / 로컬모델** — 파킹된 #18 되살려 stub→실추출기로 pet/gym **자동화**(수동 시드 대체).
- **PR 위생** — #1–#13 stale Phase-0 PR 정리. P1-4(#23) 머지.
- **후기(Phase 3)** — 세 번째 soft 속성.
