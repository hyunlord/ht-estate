# 전국 풀스택 적재 런북 (C20)

254 시군구에 대해 **단지 → 매매 → 전월세 → 조인 → geocode** 전 스테이지를 적재한다.
data.go.kr 개발계정 **1,000 호출/day** 캡 때문에 수일~수주에 걸친 **멀티데이 재개** 작업이다.
모든 스테이지가 `--resume`로 *이미 한 일을 건너뛰며* 이어진다(끊겨도 안전).

> 실제 멀티데이 *실행*은 운영자(ops) 몫. 이 문서는 명령·페이싱·재개·모니터링 절차다.

## 0. 선행
- `.env`: `DATA_GO_KR_API_KEY`(매매·전월세 활용신청 완료), `KAKAO_REST_API_KEY`(geocode).
- 시군구 코드표: `apps/api/data/regions/sigungu_kr.csv` (없으면 `--discover`로 생성).
- 모든 명령은 `apps/api/`에서 `uv run python scripts/<x>.py`.

## 1. 명령 개요
```bash
# (1회) 시군구 코드 도출 → CSV 갱신 (키 필요)
uv run python scripts/ingest_nationwide.py --discover

# 스테이지별 재개 적재 (--resume 항상 권장 — 멀티데이의 핵심)
uv run python scripts/ingest_nationwide.py --stages complex      --resume
uv run python scripts/ingest_nationwide.py --stages transaction  --resume --months 202505-202604
uv run python scripts/ingest_nationwide.py --stages rent         --resume --months 202505-202604
uv run python scripts/ingest_nationwide.py --stages join                       # 키 불필요·즉시
uv run python scripts/ingest_nationwide.py --stages geocode      --resume      # Kakao(별도 쿼터)

# 진행 모니터링 (키 불필요)
uv run python scripts/coverage_report.py
```
- `--limit N`: 이번 run 최대 N개 시군구(아직 *할 일 남은* 곳 기준 — 완료분은 limit 소모 안 함).
- `--regions 11680,41135`: 일부 시군구만.
- `--months` 생략 시 최근 12개월(전월 기준 역순).

## 2. resume가 보장하는 것 (스테이지별 self-skip)
| 스테이지 | 재개 단위 | skip 근거 |
|---|---|---|
| complex | 시군구 | 그 시군구에 단지가 이미 있으면 skip (`region_has_complex`) |
| transaction | 시군구 × **계약월** | `ingest_progress` 원장에 기록된 월 skip (0행 월도 기록 → 빈 월 재fetch 안 함) |
| rent | 시군구 × **계약월** | 〃 (stage='rent') |
| join | — | `complex_id IS NULL` 행만 재시도 (API 0, 멱등) — 항상 안전 재실행 |
| geocode | 단지 | `lat IS NULL`만 geocode (present-skip) — 항상 안전 재실행 |

→ **끊겨도 같은 명령 재실행 = 이어서.** 최악의 경우 진행 중이던 1개월만 재fetch(멱등이라 무해).
`--limit`/진행 추적은 "할 일 남은 시군구"만 세므로 매 run이 새 진척을 낸다.

## 3. 일일캡 페이싱 (개발계정 1,000 호출/day)
호출 수 ≈ **(region×month) × ceil(행수/100)** (`numOfRows=100`/page). 대략:
- **transaction**: 254 시군구 × 12개월 × ~1.5 page ≈ **~4,500 호출 → ~5일**.
- **rent**: 〃 ≈ **~4,500 호출 → ~5일**.
- **complex**: 전국 단지 detail(kaptAddr 등) 단지당 1호출. 전국 ~16–20k 단지 ≈ **~16–20일** (가장 큼).
- **geocode**: Kakao 별도 쿼터(일 10만급) — 단지 ~2만이면 **~1일**.

**권장 순서**: complex(가장 김, 먼저 꾸준히) → transaction·rent(병행 가능, 월 범위 좁혀 분할) → join(즉시) → geocode.
**일일 분할**: `--limit`로 하루 분량을 끊거나, 캡 도달 시 API가 에러를 반환하면 다음 날 같은 명령 재실행(원장이 skip).

> **운영계정 상향**: data.go.kr 활용사례 등록 시 일일캡이 크게 오른다(개발계정 1,000 → 운영계정 수만+).
> 전국 단지 detail(~16k)이 개발계정에선 병목이므로, 전국 1회 풀적재는 운영계정 승인을 권장(이 티켓 범위 밖).

## 4. 모니터링 / 진행 확인
```bash
uv run python scripts/coverage_report.py
```
시도별 **시군구 적재율 · 단지 · 매매(recall) · 전월세(recall) · geocode** + 전국 합계.
"어디까지 됐나 / 다음 어느 스테이지를 돌릴까"를 답한다. 매 run 전후로 확인.

## 5. 중단 복구
- **프로세스 종료/캡 도달/네트워크 끊김**: 같은 명령을 `--resume`로 재실행 → 완료분 skip, 이어서.
- **부분 적재 걱정 없음**: 월 단위 커밋 + 원장 기록. join/geocode는 멱등·present-skip.
- **데이터 새로고침(재fetch)이 필요할 때**: `--resume` 없이 실행하면 해당 월을 강제 재fetch(MOLIT 정정 반영).
  (원장은 resume 경로에서만 기록되므로, refresh 후 다시 resume 적재하면 정상 추적된다.)

## 6. 네트워크 회복력 (C22) — 끊김에도 안 죽고, 죽어도 자동 재개
세 층으로 인터넷 일시 끊김을 흡수한다:

1. **호출 내부 재시도(`_http`)** — 일시 오류(`ConnectError`·`ReadTimeout`·`ConnectTimeout`·DNS·5xx·429)는
   지수 백오프+지터로 재시도(기본 ~1분 라이드아웃). **영구 오류(400/401/403)는 즉시 실패**(일일캡·키
   오류를 무한 재시도로 가리지 않음). 429는 `Retry-After` 존중. `throttle`과 독립(공존).
2. **원장 체크포인트(C20)** — region×월마다 커밋·기록 → 하드 크래시/강제종료 시 **손실 ≤1개월**.
   재개하면 그 1개월만 재fetch(멱등).
3. **자동 재개 래퍼(`ingest_loop.py`)** — 긴 끊김/프로세스 종료/재부팅엔 `--resume`를 *완료까지* 반복.
   일시 오류는 백오프 재시도, 영구 오류(인가·일일캡)는 중단(다음 날/cron).
```bash
uv run python scripts/ingest_loop.py --stages transaction --months 202505-202604
uv run python scripts/ingest_loop.py --stages complex      # complex 완료까지(끊겨도 이어서)
```
**재부팅까지 견디려면 cron/systemd로 주기 기동**(래퍼가 매번 원장에서 이어받음):
```cron
*/30 * * * * cd /path/apps/api && uv run python scripts/ingest_loop.py --stages transaction >> ingest.log 2>&1
```
일일캡(`PublicDataError "22"`) 도달 시 래퍼는 중단 → 다음 날 cron이 자동 재개.

## 7. 검증(키리스)
resume/skip·원장·커버리지·**재시도/백오프(일시)·빠른실패(영구)·래퍼 루프**는
`tests/test_progress_repo.py`·`test_ingest_runner.py`(resume·체크포인트)·`test_ingest_nationwide.py`·
`test_coverage_report.py`·`test_http.py`(C22 재시도)·`test_ingest_loop.py`로 키 없이 검증된다(`make gate-api`).
