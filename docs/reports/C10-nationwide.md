# 리턴팩 C10 — 전국 확장 적재

## 변경 요약
Tier-1 적재를 서울(3,373) → **전국**으로 확장. C9 서울 헬퍼(`run_batch`·공유 throttle·재개)를
재사용하고, **시군구 코드를 하드코딩하지 않고 K-apt에서 도출**한다(getTotalAptList의 bjdCode
앞 5자리 = 시군구코드 → 항상 최신·날조 0). 도출 결과를 `data/regions/sigungu_kr.csv`로 커밋
(리뷰·재현용). 전국은 일일한도·Kakao rate로 multi-run — `--resume`로 누적, **한 run 완료가
목표 아님(진행+재개가 DoD)**. enrichment 시드 미변경. 적재 DB는 gitignore(미커밋).

## 코드 리스트 (도출 — 254 시군구 · 17 시도)
`scripts/ingest_nationwide.py --discover` → K-apt 전국 단지목록(22,177 단지)에서 distinct
시군구코드 추출 → CSV. **17개 시도 전부 커버**(세종·제주 엣지 포함), 서울 25구는 C9 `SEOUL_25`와 일치.

| 시도 | 시군구 | 시도 | 시군구 |
|---|---|---|---|
| 11 서울 | 25 | 41 경기 | 47 |
| 26 부산 | 16 | 43 충북 | 14 |
| 27 대구 | 9 | 44 충남 | 16 |
| 28 인천 | 10 | 46 전남 | 21 |
| 29 광주 | 5 | 47 경북 | 23 |
| 30 대전 | 5 | 48 경남 | 22 |
| 31 울산 | 5 | 50 제주 | 2 |
| 36 세종 | 1 | 51 강원 | 18 |
| | | 52 전북 | 15 |

합계 **254 시군구** (시도명은 강원·전북특별자치도 등 현행 행정명 반영 — 도출이 최신임을 확인).

## 헬퍼 (C9 확장)
`scripts/ingest_nationwide.py`:
- `discover_sigungu(api_key)` — K-apt total 목록 → distinct 시군구(bjd 앞5)+대표 시도/시군구명.
- `save_codes`/`load_codes` — CSV 왕복(code,sido,sigungu).
- `loaded_sigungu(conn)` + `--resume` — 이미 complex 적재된 시군구 skip(**complex 스테이지 전용** 가드).
- `run_batch`(C9 재사용, 공유 throttle) + `coverage_table` + `sido_summary`(시도별 집계).
- `--limit` — 이번 run 시군구 수 상한(멀티런 분할).

## 진행 (이번 세션 — 멀티런 중 1회)
- `--discover` 완료: 254 시군구 CSV 커밋.
- `--stages complex --resume` 실행 중: 서울 25구 skip 후 전국 complex 누적.
- **스냅샷(리포트 시점)**: 서울 25구(3,373) 완료 + 부산 진행 → 30/254 시군구·~3,650 단지(백그라운드
  계속 증가 중). 서울→부산으로 전국 루프 동작 확인. 나머지 시군구는 재개로 누적.
- **완료 아님 — 의도된 부분 적재**(일일한도·시간). geocode가 서울 3,338에 ~49분이었으니 전국은
  수 시간(+거래) → 여러 run/day 분산.

## 재개 명령
```
# complex 누적(미적재 시군구만 — 매 run마다 줄어듦):
PYTHONPATH=. uv run python scripts/ingest_nationwide.py --stages complex --resume
# complex 다 된 뒤 거래+조인(멱등 재실행):
PYTHONPATH=. uv run python scripts/ingest_nationwide.py --stages transaction,join
# 좌표(skip-if-present — 여러 날 분산 안전):
PYTHONPATH=. uv run python scripts/ingest_nationwide.py --stages geocode
# 코드표 갱신(행정구역 변경 시):
PYTHONPATH=. uv run python scripts/ingest_nationwide.py --discover
```

## 멱등 / 재개 / 회귀
- 전 스테이지 idempotent(complex upsert · txn 결정론 id · join 미매칭만 · geocode skip-if-present).
- `--resume` complex-skip으로 **서울/강남 회귀 0**(이미 적재분 재fetch 안 함, 데이터 불변).
- `--resume`를 transaction/geocode-only run에 쓰면 함정(complex 있는 시군구 전부 skip) → 가드로 차단.

## 객관 게이트 (raw)
- ruff `All checks passed!` · pyright `0 errors` · pytest `212 passed`(nationwide 7 신규, 회귀 0) · e2e `5 passed`.
- `make gate` green — 헬퍼는 키리스 mock 테스트(discover·CSV왕복·resume skip·가드·시도집계).

## watch (블로커 아님)
- **SQLite 규모**: 전국 ~22k 단지 + 수십만 거래는 인덱스로 OK. bbox/geo 쿼리 저하·멀티유저 시
  Postgres+PostGIS(설계 승급) — C10 필수 아님.
- **data.go.kr 일일한도**: 전국이면 multi-day(재개). 한도 상향은 활용신청.

## 미해결 / 결정필요
- 전국 적재 **진행 중(부분)** — 재개로 누적. 잔여 시군구는 위 재개 명령.
- 전월세: 별도 feature(이번은 매매). join 미매칭(~23%, R2 후속). 로더/헬퍼 `PYTHONPATH=.` papercut(C7~).

## 다음 제안
- **잔여 적재 재개** — `--stages complex --resume` 반복 → 전국 complex 완료 후 txn/geocode.
- **전월세 추가** — MOLIT 전월세(새 feature).
- **enrichment 자동화** — 파킹 #18 되살려 gym/pet를 전국 단지로 lazy 자동(수동 시드 한계 해소).
- **Postgres 승급 검토** — 전국 규모 굳으면 bbox/geo 성능 측정 후 결정.
