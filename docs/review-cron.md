# review 후기 cron — 운영 가이드 (human commit gate)

review 라이브 품질 게이트 **GO**(grounded 7/8·저작권 캡 0초과·소스 legit, `docs/reports/review-batch/`)
확인 후, 후기 실수집을 **저volume cron + human commit gate**로 골격화한다. review는 **표시 전용**
(검색 랭킹 신호 아님 — `SoftSpec=={gym,pet}`). cron은 이를 바꾸지 않는다.

## 핵심 불변식 — 자동은 staging까지만
`scripts/review_cron.py`(= `make review-cron`)는 **staging JSONL에만** 쓴다. **라이브 DB write·git
commit을 하지 않는다.** 코드로 강제:
- 모듈이 DB writer(`load_seed`/`write_facts`)를 **import조차 안 함** — DB 접근은 `select_candidates`
  (읽기 전용)뿐. (테스트 `test_review_cron.py`가 enrichment write 0·writer 미노출을 가드.)
- 기본 staging 경로 `apps/api/data/staging/review.jsonl`는 **gitignore** → 무검토 자동 출력이
  자동으로 추적/적재되지 않음(자동 git 진입 구조적 차단).

## 자동 단계 (cron)
```bash
make review-cron LIMIT=20                      # 미적재·미staging 후보 20단지 → staging append
# 또는 직접:
cd apps/api && uv run python scripts/review_cron.py --limit 20 --max-turns 80
```
- **선택**: review가 (i) DB에 fresh하지 않고 (ii) 아직 staging되지 않은 단지를, 세대수 desc로 N개.
- **멱등·재개**: 중간에 끊겨도 다음 run이 DB-fresh + staging된 단지를 둘 다 skip해 이어간다
  (재질의·중복 추출 방지). staging append는 `(complex_id, source_url)` dedup(다출처는 줄 분리 허용).

## cron / systemd timer 예시 (야간 저volume·시차)
```cron
# 매일 04:10 review 15단지 — 구독 rate 보호 위해 소량. 미적재·미staging만 → 점진 누적.
10 4 * * *  cd /path/to/ht-estate && make review-cron LIMIT=15 >> /var/log/ht-review-cron.log 2>&1
```
```ini
# systemd: ht-review-cron.service + .timer (OnCalendar=*-*-* 04:10:00)
[Service]
Type=oneshot
WorkingDirectory=/path/to/ht-estate
ExecStart=/usr/bin/make review-cron LIMIT=15
```

### 보수적 N (latency)
라이브 게이트 실측 ≈ **552s/8단지 ≈ 69s/단지**(후기 다출처 탐색 비용). 1콜로 모든 후보를 처리하므로
**N을 작게**(권장 10~20) 잡고 `--max-turns`를 넉넉히(80+). 더 키우려면 N을 늘리기보다 **여러 시차
배치**로 나눠라(구독 rate·turn 한도 보호). gym/pet `auto-enrich`보다 단지당 느리다.

## 사람 단계 — promote (human commit gate)
cron이 채운 staging을 **사람이 검수한 뒤에만** 시드 commit·DB 적재한다(무검토 자동 commit 금지):
1. **spot-audit** — `apps/api/data/staging/review.jsonl`의 표본 `source_url`을 직접 열어 summary와
   대조(grounded? 환각/오귀속 아님? 저작권 캡? 차단도메인 아님?). 후기는 주관·표시 전용임을 유지.
2. **promote → seed** — 검증 통과한 줄만 `apps/api/data/seeds/review_gangnam.jsonl`로 옮기고
   (불량 줄은 버린다) `git add` + commit(**사람 리뷰 후 커밋** — 자동 아님).
3. **DB 적재** — 시드를 enrichment에 적재(라이브 write — 사람 트리거):
   ```bash
   make load-review            # = scripts/load_review_seed.py (멱등·fresh-skip)
   ```
   적재되면 그 단지는 DB-fresh가 되어 다음 cron이 자동 skip한다(중복 적재 0).

> 적재용 `make load-review` 타겟이 없으면 `cd apps/api && uv run python scripts/load_review_seed.py`.

## 안전장치 (무검토 자동화 — 정직한 한계)
1. **프롬프트 규율**(`scripts/prompts/enrich_review.md`) — 원문복제 금지·요약 짧게·출처 필수·
   차단도메인 금지·근거없음 drop·보수적 confidence·오귀속 배제.
2. **파서 강제**(`parse_review_output`) — 환각 단지/차단도메인/출처없음/빈 요약 drop ·
   `(complex_id, source_url)` dedup · 저작권 길이 캡(220자) 절단 · 위키 출처 confidence cap.
3. **staging 격리** — 자동 출력은 gitignore된 staging에만. DB·git은 사람이 promote할 때만.
4. **spot-audit 권장** — 라이브 첫 배치는 특히 표본 `source_url`을 web_fetch로 검증.

## 한계
- **커버리지 천장**: 공개 후기가 없는 단지(예: 일부 공공임대)는 drop된다(날조 금지). 게이트 표본에서
  8단지 중 1단지(수서1-1)가 후기 미발견 drop.
- **rate**: 구독 한도라 run당 소량(10~20). 대량 일괄 금지.
- **다음**: 전국 적재 마감 후, gym/pet도 같은 staging+gate 패턴으로 동형 확장 가능(현재 `auto_enrich`는
  DB 직적재 — review만 staging-gate 적용).
