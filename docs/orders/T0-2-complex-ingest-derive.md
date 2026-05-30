# 의뢰서 T0-2 — 단지정보 적재: 라이브 태그검증 + has_gym·parking_ratio 파생

## 목표
K-apt 단지정보를 라이브 응답으로 검증해 파서 태그 매핑을 확정(T0-1 #1 리스크 해소)하고,
파생(has_gym·parking_ratio)을 뽑아 `complex` 행으로 적재(provenance 포함). `.env` 자동로딩 배선.

## 범위
- IN (순서대로):
  - A. `.env` 로딩 배선 (load_dotenv 또는 pydantic-settings). 테스트는 키 없이 동작.
  - B. 라이브 태그검증(키 1회성, 게이트 아님): list_complexes→fetch_complex_info→fetch_trades 실호출,
    실태그 vs 파서 대조, 다르면 kapt.py(필요시 molit.py) 수정, **fixture를 실캡처본으로 교체**.
  - C. 파생 + 적재: has_gym(gym 한정 키워드), parking_ratio(None/0 세이프), upsert_complex(provenance·멱등).
- OUT: 실거래 적재·퍼지 조인·지오코딩·hard filter·UI(T0-3+). 전국 벌크 적재. 게이트 라이브 호출.

## 수용 기준 (DoD)
- [ ] `.env`의 DATA_GO_KR_API_KEY 자동 로드(export 없이). 미설정 시 명확한 에러 유지.
- [ ] 라이브 검증 완료: 실응답으로 태그 확정, fixture가 실캡처본. 리턴팩에 확정 태그 매핑표.
- [ ] has_gym pos/neg. parking_ratio 정상 + household None/0 → None.
- [ ] upsert_complex: complex 행 적재, provenance(source_url·updated_at), 멱등. :memory: 테스트.
- [ ] make gate 전체 green(키 없이 도는 상태 유지).

## 프로토콜
- §7 PLAN→DEBATE→CHALLENGE + §8.2 풀 루브릭(≥95 + 하한).
- 선행: PR #1·#2 미머지면 T0-1 위에 스택.

## 산출물
- 브랜치 feat/T0-2-complex-ingest-derive, PR. 리턴팩(§9) + 확정 태그 매핑표.
- 다음 제안: T0-3 실거래 적재.
