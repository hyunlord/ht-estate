# enrichment cron — 운영 가이드 (enrich-cron-gate)

미적재 단지의 gym/pet/review를 `claude -p`(headless, **구독 인증** — Anthropic API 키 불필요)로 추출해
**점진 누적**한다. **enrich-cron-gate 이후 자동은 staging까지만(human commit gate)** — `scripts/enrich_cron.py`
(`make enrich-cron`)가 미적재 후보를 추출해 **`data/staging/<attr>.jsonl`(gitignored)에만** 쓴다.
**라이브 DB write·git commit은 사람이 spot-audit 후 promote할 때만.** 무검토 자동 DB write 0(코드강제:
cron 모듈이 DB writer를 import 안 함).

> ⚠ **gym/pet은 랭킹 신호**(`SoftSpec=={gym,pet}`). 무검토 자동 적재가 랭킹을 조용히 바꾸지 않게,
> 표시 전용 review와 동일하게 **사람 promote 전엔 랭킹 반영 0**. (구 `auto_enrich.py` CLI의 DB 직적재는
> staging-only로 강등됨 — `auto_enrich()` 함수는 테스트·명시적 수동 적재 building block으로만 잔존.)

## 전제
- `claude` CLI가 구독 인증된 상태(headless `claude -p`가 동작).
- DB에 단지 적재돼 있어야 함(C9/C10 ingest). enrichment store(P1-1)·로더(C4/C5)·`_bootstrap`(C12).
- `auto_enrich.py`는 `_bootstrap`으로 sys.path를 처리 → `PYTHONPATH` 불필요.

## 자동 단계 — cron (staging까지만)
```bash
make enrich-cron ATTR=gym LIMIT=20         # gym 20단지 → data/staging/gym.jsonl
make enrich-cron ATTR=pet LIMIT=20         # pet → data/staging/pet.jsonl
make enrich-cron ATTR=review LIMIT=15      # review → data/staging/review.jsonl
# 또는 직접:
cd apps/api && uv run python scripts/enrich_cron.py --attribute gym --limit 20 --max-turns 80
```
선택: 해당 속성이 (i) DB에 fresh하지 않고 (ii) 아직 staging되지 않은 단지를 세대수 desc로 N개.
**멱등·재개**: DB-fresh + staging된 단지를 둘 다 skip → 끊겨도 이어감. staging append는
`(complex_id, source_url)` dedup.

## cron 예시 (야간 저volume·누적)
```cron
# 매일 03:10 gym, 03:40 pet, 04:10 review — 구독 rate 보호 위해 소량·시차. staging까지만.
10 3 * * *  cd /path/to/ht-estate && make enrich-cron ATTR=gym    LIMIT=20 >> /var/log/ht-enrich.log 2>&1
40 3 * * *  cd /path/to/ht-estate && make enrich-cron ATTR=pet    LIMIT=20 >> /var/log/ht-enrich.log 2>&1
10 4 * * *  cd /path/to/ht-estate && make enrich-cron ATTR=review LIMIT=15 >> /var/log/ht-enrich.log 2>&1
```
미적재·미staging만 선택하므로 매일 돌면 staging이 **점진 누적**된다. review는 단지당 ~69s로 느리니 N을 작게.

## 사람 단계 — promote (human commit gate)
cron이 채운 staging은 **사람이 검수한 뒤에만** 시드 commit·DB 적재한다(무검토 자동 적재 금지). 속성별:
1. **spot-audit** — `data/staging/<attr>.jsonl` 표본 `source_url`↔값 대조(grounded? 환각/오귀속 아님?
   차단도메인 아님?). **gym/pet은 랭킹에 반영되므로 특히 엄격히**(잘못된 yes가 순위를 올림).
2. **promote → seed** — 통과한 줄만 `data/seeds/<attr>_*.jsonl`로 옮기고 **사람이 git commit**.
3. **DB 적재**(라이브 write — 사람 트리거): `make load-gym` / `make load-pet` / `make load-review`
   (= `load_<attr>_seed.py`, 멱등). 적재되면 DB-fresh가 되어 다음 cron이 자동 skip(중복 0).

## 안전장치 (무검토 자동화 — 정직한 한계)
인터랙티브 (a)는 매 배치 사람/Web이 검수했으나 cron은 **무인**이다. 따라서:
1. **보수적 프롬프트** — 애매하면 `unknown`(`scripts/prompts/enrich_{gym,pet}.md`에 규율 인코딩:
   단지내-vs-상업 구별 · no-signal→unknown · 차단도메인 금지 · 약한출처 단독 yes 금지 · 오귀속 배제).
2. **파서가 규율 강제**(`parse_output`) — 프롬프트를 어겨도 코드가 막는다:
   차단도메인(naver/hogangnono/asil) source drop · 상태 도메인 밖→unknown 강등 · 환각 단지(요청 후보 밖) drop ·
   source_url/evidence 없으면 drop · pet `confirm_with_office` 전수 true 강제.
3. **출처 전수 기록** — 모든 fact가 `source_url`(http 또는 `urn:ht-estate:auto:<id>`)을 들고 감 → 감사 가능.
4. **주기적 spot-audit 권장** — C4/C5/C7처럼 샘플 `source_url`을 사람이 web_fetch로 검증. 특히 `yes`/`conditional`.
5. **무검토 자동 DB write 0(코드강제 게이트)** — `enrich_cron`은 staging(`data/staging/`, gitignore)에만
   쓴다. DB writer(`write_facts`/`load_*_seed`)를 **import조차 안 함** → 무인 경로가 라이브 DB·랭킹·git에
   진입 불가. 적재·commit은 사람 promote 단계에서만(위 "사람 단계").

## 한계
- **pet 천장**(§11): 자동화해도 공개 per-complex 반려동물 정책이 없는 단지는 `unknown`. gym·후기엔 효과 큼.
- **무검토 품질**: spot-audit로 보완. 라이브 첫 배치는 특히 샘플 검증 권장.
- **rate**: 구독 한도라 run당 소량(20~50). 대량 일괄 금지.
