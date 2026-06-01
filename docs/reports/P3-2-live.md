# 리턴팩 — P3-2-live · 실-LH floorplan 실적재 producer

브랜치 `feat/P3-2-live` (base `main` `4f0e715`). 분류: small feature(producer), §8.2 약식.

## 무엇을 했나
스파이크 `floorplan_poc --run`이 콘솔 print만 하던 것을 **검증 가능한 producer**로 보강.
실 LH 평면도를 ① 이미지 파일로 남기고(Web 육안 대조) ② `load_floorplan_seed` 호환 seed로
기록하는 두 구멍을 메웠다. 실 `--run`(키·`claude -p`)은 그대로 사용자 ops.

## 변경 (2 파일 · +283/-10)
- `apps/api/scripts/spikes/floorplan_poc.py` — `--out <dir>` 추가 + producer 헬퍼.
- `apps/api/tests/test_floorplan_poc.py` — 키리스 producer 테스트 8개 추가(기존 3 → 11).

## A. `--out <dir>` (이미지 + seed)
`floorplan_poc.py --run --out <dir>` 시:
- **이미지**: 각 표본 디코드 바이트를 `<dir>/lh_<id>.png`로 저장(`save_image`). `<id>`는 레코드의
  id 후보 키(`id/seq/no/mgmNo/hsmpSn/bldgSn`) 또는 1-based 순번 폴백(`record_id`). 파일명 `.png`
  고정(주문서 DoD·Web 검증 경로 안정).
- **seed**: 조인 매칭된 표본만 seed 후보(`to_seed_record`)로 만들어 **`auto_enrich.parse_floorplan_output`
  에 그대로 태운 뒤** `append_seed`로 `<dir>/floorplan_seed.jsonl`에 기록. 즉 producer가 쓰는 seed는
  auto_enrich의 floorplan 경로와 **동일 파서**를 거치므로 `load_floorplan_seed` 호환이 구조적으로 보장됨
  (형식·§11 규율 중복 0).
- **provenance**: `source_url`은 레코드 URL(http/urn) 우선, 없으면 `urn:lh:15037046:<id>` 폴백
  (`record_source_url`) — 파서가 urn:을 통과시켜 출처 누락 방지. `source_type="official"`(LH 정부
  공개데이터 = 공식 출처), `confidence=0.6`(공식 이미지+VLM 판독 신뢰 — 주관 점수 아님 §11).

seed 레코드 형식(= `parse_floorplan_output` 출력 = `load_floorplan_seed` 입력):
```json
{"complex_id":"K1","name":"LH수서1단지","bay":3,"orientation":"남향","structure":"판상형",
 "evidence":"전면 3실","confidence":0.6,"source_type":"official","source_url":"urn:lh:15037046:111"}
```

## B. 조인 커버리지 요약
`--run`이 끝나면 항상 출력(`join_coverage`+`format_coverage`):
- `matched/total (율%)` — 표본 LH 평면도 중 complex 매칭 수/율.
- **LH-접두 정규화 효과** — 이름이 선행 'LH' 접두를 가진 표본 수 / 그중 매칭 수. 이 그룹이
  `normalize_name`의 `^lh` 제거(P3-2) 수혜 대상이다(스파이크가 발견한 sim 0.83<0.85 비대칭을 메운 곳).
  반사실(정규화 없을 때)을 따로 계산하진 않음 — 정직하게 "수혜 그룹 매칭"만 보고.

## C. 키리스 테스트 (라이브 mock)
`tests/test_floorplan_poc.py` (전부 키리스 — 다운로드·`claude -p`·키 mock):
- `save_image` 경로(`lh_<id>.png`)·바이트 보존.
- `record_id`(id 키/순번 폴백)·`record_source_url`(http 통과 / urn 폴백).
- `join_coverage` 산수 + LH-접두 효과 + 0건 zero-division 안전.
- **seed↔로더 round-trip**: `to_seed_record → parse_floorplan_output → append_seed → load_seed_records
  → load_seed → read_facts` end-to-end로 `load_floorplan_seed` 호환 증명.
- **§11 규율 재확인**: feature 전부 null / 점수화 토큰('좋은구조')은 파서가 drop/null.
- **--out 통합**: get_api_key·fetch_inventory·extract_features mock + 파일 DB 1단지 preseed →
  `main(["--run","--out",...,"--db",...])` → 이미지 2장 저장 / 매칭 K1만 seed 1행(urn provenance) /
  무관 단지(신림동) 미기록 / 커버리지 `1/2`. 산출 seed를 다시 `load_floorplan_seed`로 적재까지 확인.

## 객관 게이트
`make gate-api` green — ruff clean · pyright 0 errors · **pytest 343 passed**.
- **랭킹 불변**: `test_search_floorplan.py::test_floorplan_is_never_a_ranking_signal` 통과
  (`set(SoftSpec.model_fields)=={"gym","pet"}`, floorplan 부착해도 순서 불변).
- **회귀 0**: 기존 343 전부 green. web/shared 무변경(diff는 `apps/api/scripts`+테스트 한정) → web/e2e 무영향.

## 사용자 ops (이 티켓 머지 후)
1. 실 산출: `.env`(DATA_GO_KR 키) + `claude` CLI 준비 후
   ```
   cd apps/api && DATA_GO_KR_API_KEY=... uv run python scripts/spikes/floorplan_poc.py \
     --run --limit 10 --out ../../docs/reports/floorplan-real/
   ```
   → `docs/reports/floorplan-real/lh_*.png` + `floorplan_seed.jsonl` + 조인 커버리지 출력.
   ⚠ `fetch_inventory`의 odcloud 엔드포인트/이미지 필드는 라이브 미확정 가정 — 첫 run에서
   실제 스키마 확인 필요(빈 결과면 `_IMG/_NAME/_ADDR/_ID/_URL_KEYS` 보정).
2. 이미지+seed+커버리지를 커밋/첨부 → Web가 **실 LH 이미지 ↔ 추출 육안 대조** + 조인 커버리지 타당성 검증.
3. (Web 검증 후) `uv run python scripts/auto_enrich.py --attribute floorplan` 로 seed 적재
   → enrichment 채움.

## 다음
- 사용자 실 run → Web 실 이미지 대조 검증 → auto_enrich 적재.
- 후속: review/floorplan cron prefill, 라이브 엔드포인트 확정 후 producer 상수 보정.
