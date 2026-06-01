# P3-2-live-run — 실-LH floorplan 라이브 1회: **스키마 확정 + 막힘 진단**

브랜치 `feat/P3-2-live`(#40). 성격: 라이브 ops run + 스키마 확정(R3-ops/진단).
**outcome = 분기 (b)의 더 깊은 형태 — 추정 스키마가 틀렸을 뿐 아니라 접근 방식(파일 vs API)이 다르고,
실데이터가 로그인 게이트로 막혀 있다.** 라이브 데이터(분기 a)는 산출 불가 → Web/사람과 진단·재설계.

## 1. 실행 / 관측 (evidence)
명령(의뢰서 그대로):
```
cd apps/api && uv run python scripts/spikes/floorplan_poc.py \
  --run --limit 10 --out ../../docs/reports/floorplan-real/
```
- 키: `get_api_key()`가 루트 `.env`의 88자 decoded serviceKey resolve(정상).
- `fetch_inventory` → 추정 odcloud 엔드포인트 호출 →
  **HTTP 400** `{"code":-3,"msg":"등록되지 않은 서비스 입니다."}`
  ```
  GET https://api.odcloud.kr/api/15037046/v1/uddi?serviceKey=<REDACTED>&page=1&perPage=10
  → 400 Bad Request
  ```
- `fetch_text`는 영구 4xx를 즉시 raise → (수정 전) raw traceback. **이미지/seed/커버리지 산출 0**(out 디렉터리도 생성 안 됨 — 루프 진입 전 실패).

## 2. 확정된 실제 스키마 (data.go.kr 15037046)
데이터셋 페이지·메타데이터(`/catalog/15037046/fileData.json`) 확정:

| 항목 | 추정(producer) | **실제(확정)** |
|---|---|---|
| 접근 방식 | 오픈API(odcloud, page/perPage 페이징) | **1회성 파일 다운로드**(`제공형태: 공공데이터포털에서 다운로드(원문파일등록)`) |
| 오픈API 유무 | 있다고 가정 | **없음** — `api.odcloud.kr/api/15037046`은 "등록되지 않은 서비스" |
| 인가 | serviceKey | **포털 로그인 세션**(serviceKey는 파일 다운로드 비인가) |
| 신선도 | (가정 없음) | **2021-08-26 vintage·1회성**(수시, 비주기) |
| 파일 구성 | 단일 JSON 레코드(이미지+단지명+주소) | **CSV(평면도 파일별 현황) + JSON(평면도 이미지)** 2종 |
| JSON 레코드 필드 | `{data/image, 단지명, 주소, …}` 추정 | **정확히 `{image, mime, data, width, height}`** — `data`=base64 이미지, **단지명·주소 없음** |
| 조인 키 위치 | 이미지 레코드 안 | **CSV에만**(단지명·주소·공급면적); JSON과는 `image`(파일명)로 조인 |

원문(데이터셋 설명): *"평면도 파일별 현황을 CSV 형태로, 평면도는 JSON형태로 제공 … json의 속성은
image, mime, data, width, height로 구성"*, *"구글에 base64 to png … "data":" 다음 부분 …"*.

## 3. 막힌 지점 (왜 분기 a 불가, 왜 "상수 보정"으로 안 풀리나)
1. **오픈API 부재** — `fetch_inventory`가 전제한 페이징 JSON API가 존재하지 않는다. 고칠 엔드포인트
   상수가 없다(= 단순 `_IMG/_NAME/_ADDR/_ID/_URL_KEYS` 보정으로 해결 불가).
2. **로그인 게이트** — 파일은 포털 로그인 세션으로만 받는다. 보유한 serviceKey는 오픈API용이라
   파일 다운로드를 인가하지 않는다. 헤드리스/키 기반 자동 fetch 불가.
3. **레코드 형상 불일치** — JSON 이미지 레코드에 단지명·주소가 없다(`image,mime,data,width,height`).
   조인 키는 별도 CSV에 있고 `image`(파일명)로 묶어야 한다 → `parse_floorplan_record`(이미지
   레코드에서 name/addr 추출 전제)와 구조적으로 어긋난다.

→ 이 세 가지는 **구조적**이라 검증 가능한 "상수 보정 → 재실행" 루프로 닫히지 않는다. 재실행해도
같은 벽(400)에 부딪힌다. **재실행 중단, 에스컬레이션.**

## 4. 이 런에서 한 코드 변경 (verifiable·behavior-safe)
실데이터를 못 만들었으므로 **추측성 구조 재작성은 push하지 않음**(검증 불가 + OUT 스코프).
대신 *확정된 사실*만 코드에 반영(키리스 게이트 green 유지):
- `_IMG/_NAME/_ADDR_KEYS` 주석을 확정 스키마(`{image,mime,data,width,height}`·CSV 조인)로 교정.
- `fetch_inventory` docstring을 "추정·라이브 확정 전" → "확정: 파일 데이터셋·로그인 게이트·400 raise"로 교정.
- `main`: 확정-사망 엔드포인트에서 **raw traceback 대신 진단 출력 + `rc=3`**(키없음 `rc=2`와 구분).
  serviceKey는 출력에서 `<REDACTED>` 처리(리다이렉트 시 키 유출 차단).
- 키리스 테스트 1개 추가(`test_run_blocked_endpoint_prints_diagnosis_not_traceback`).
- `make gate-api` green: ruff clean · pyright 0 · **pytest 344 passed** · 랭킹 불변·회귀 0.

## 5. 실 도면 vision robustness 자가평가
**미수행 — 막힘.** 실 LH 도면(스캔·치수선·미표기)을 한 장도 확보 못 해(로그인 게이트) `claude -p`
비전 추출을 실 데이터로 돌리지 못했다. 합성 1x1 PNG·proxy로는 robustness를 검증할 수 없다.
정직하게: **floorplan VLM 추출의 실 도면 정확도/ null 규율 견고성은 아직 미검증.** (parse_floorplan_output
의 §11 규율은 코드로 강제되나, 그건 "환각·점수화 거부"이지 "스캔 잡음 robustness"가 아니다.)

## 6. 권고 (재설계 — Web/사람 결정 필요)
- **(권장) 후보-온디맨드 경로로 피벗** — `auto_enrich --attribute floorplan`(이미 구현·키리스,
  `claude -p` WebSearch/WebFetch)이 hard-filter 통과 후보(~20)에만 평면도를 리서치한다. 이는
  CLAUDE.md 설계 원칙 **3(lazy 추출은 후보에만·전국 벌크 금지)**와 정합 — 15037046 벌크-seed
  producer는 애초에 이 원칙과 긴장 관계였다. 15037046 없이도 floorplan enrichment가 가능.
- **(대안, 무거움) 파일기반 producer 재설계** — 포털 로그인으로 CSV+JSON 2파일을 수동/세션
  다운로드 → `image`(파일명)로 CSV↔JSON 조인 → 단지명/주소로 K-apt 매칭 → VLM. 단점: 로그인
  자동화·1회성(2021) 데이터·전국 벌크라 원칙 3과 충돌. 1회 수동 다운로드 후 오프라인 적재면 가능.
- **(보강) vision robustness** — 어느 경로든 실 도면 표본 3~5장으로 추출 정확도·null 규율을 한 번
  육안 검증한 뒤 적재(현재 미검증).

## 7. 다음
- **Web/사람 진단**: 경로 결정 — (권장) on-demand `auto_enrich --attribute floorplan`로 floorplan을
  채울지, (대안) 파일 다운로드 producer를 재설계할지.
- 15037046 벌크-API 전제는 **폐기**(확정). producer 골격(#40)의 키리스 검증/이미지·seed 산출 로직은
  유효 — 입력원만 위 경로 중 하나로 교체하면 재사용 가능.
- **OUT(불변)**: C27 머지·`auto_enrich --attribute floorplan` 적재는 경로 결정 + (실 표본) Web 검증 후.
