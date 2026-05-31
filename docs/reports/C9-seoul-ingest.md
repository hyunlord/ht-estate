# 리턴팩 C9 — 범위 확장 적재 (서울 전체 25구)

## 변경 요약
Tier-1 적재 범위를 강남3구(609) → **서울 전체 25구(3,373 단지)**로 확장. 각 구를 풀 스테이지
(complex→transaction→join→geocode)로 적재. 코드는 **지역 루프 헬퍼**(`scripts/ingest_seoul.py`)
1개만 추가 — 새 적재 로직 없이 C3 `run_ingest`를 25구 리스트로 감싸고 하나의 Throttle을 전 지역에
공유. 멱등·재개. enrichment 시드(gym/pet)는 미변경(별개). 적재 DB는 gitignore(미커밋).

## 적재 방법
- `scripts/ingest_seoul.py` — `SEOUL_25` 코드 리스트 루프 → `run_ingest`(공유 Throttle). 전국 확장은
  리스트에 시군구 코드만 추가. 스테이지별 실행: `--stages complex` → `transaction,join` → `geocode`
  (top DoD인 complex 전구 완료를 먼저 보장, 깊은 스테이지는 best-effort·재개).
- 실행(최근 12개월=202505–202604):
  `PYTHONPATH=. uv run python scripts/ingest_seoul.py --stages complex`
  `… --stages transaction,join` · `… --stages geocode`

## 커버리지 (서울 25구)
| 지표 | 값 |
|---|---|
| 단지(complex) | **3,373** (25/25 구) |
| 거래(transaction, 최근 12개월) | **78,005** |
| 조인 recall (txn→complex) | **60,138 / 78,005 = 77%** |
| geocode | **3,338 / 3,338 geocodable = 100%** (전체 3,373 중 98%; 35개는 road_addr 부재) |

측정 달: **202505–202604**(최근 12개월, today=2026-05-31 기준 직전 완료월부터 12개).

### 구별 (단지 · geocoded)
```
11110 종로 38/38   11140 중 49/47    11170 용산 86/82   11200 성동 131/130
11215 광진 96/95   11230 동대문 131/129  11260 중랑 135/134  11290 성북 146/144
11305 강북 56/55   11320 도봉 119/119  11350 노원 230/230  11380 은평 161/160
11410 서대문 94/93  11440 마포 140/140  11470 양천 152/150  11500 강서 211/211
11530 구로 178/177  11545 금천 52/51    11560 영등포 179/177  11590 동작 138/136
11620 관악 91/89   11650 서초 199/197  11680 강남 231/227  11710 송파 178/178
11740 강동 150/149
```
강남3구(11650·11680·11710 = 199·231·178)는 C7 complex 위에 transaction+geocode **보강 완료**(C7은 complex만이었음). **강남3구 회귀 0** — 단지 수 유지·좌표 추가.

## 수치 타당성
- 구별 단지 수 38~232: K-apt 등록 단지 규모와 합리적(노원 230·강남 231 대단지 밀집, 종로 38·금천 52 소규모).
- geocode율 ~98–100%: 도로명주소 보유 단지는 사실상 전부 좌표 획득(영구 캐시·skip-if-present).
- join recall 77%: R2(강남 지번매칭 68.5%) 대비 양호 — 서울 전역 이름+법정동 매칭 표본이 더 큼. (측정: complex_id 부착된 거래 / 전체 거래, 202505–202604.)

## 객관 게이트 (raw)
- ruff `All checks passed!` · pyright `0 errors` · pytest `205 passed`(헬퍼 6 신규 포함, 회귀 0) · e2e `5 passed`.
- `make gate` green — 적재 헬퍼는 키리스 mock 테스트(run_ingest 위임 검증).

## 재개 / 멱등
- 전 스테이지 idempotent: complex upsert · transaction 결정론 txn_id upsert · join backfill(미매칭만) · geocode skip-if-present(lat IS NULL만).
- 중단 시 같은 명령 재실행이 이어감(검증: 헬퍼 dry-run 종로구 재실행 시 단지 수 불변).

## 미해결 / 결정필요
- **전월세**: 이번은 매매(MOLIT 아파트 매매)만. 전월세는 별도 거래유형/엔드포인트(별도 feature).
- **join 미매칭 23%**: 신축/이름변형/지번 케이스 — T0-4 퍼지조인 후속 튜닝 여지(R2 분석 연장).
- **로더 PYTHONPATH papercut**(C7~): `scripts/` 실행 시 `PYTHONPATH=.` 필요. 부트스트랩/Makefile 타겟 권장.
- 적재 DB는 미커밋(gitignore) — 재현은 키로 재적재. 헬퍼+리포트만 PR.

## 다음 제안
- **전국 확장** — `SEOUL_25`에 타 시군구 코드 추가(같은 헬퍼). geocode는 여러 run 분산(일일한도).
- **전월세 추가** — 별도 거래유형 적재(MOLIT 전월세 API) — 새 feature 티켓.
- **enrichment 자동화** — 파킹 #18(P1-2 실추출기) 되살려 gym/pet를 3,373 단지로 lazy 자동 확장(수동 시드 대체).
- **PR 위생** — #1–#13 stale + 로더 papercut.
