# enrichment 자동 prefill — cron 운영 가이드 (C12)

수동 (a) 시드(C4/C5/C7)를 무인 자동화한다. cron이 `scripts/auto_enrich.py`를 돌려 미적재 단지의
gym/pet을 `claude -p`(headless, **구독 인증** — Anthropic API 키 불필요, marginal-free)로 추출 →
시드 append → 로더 적재. **배치 prefill**(라이브 lazy 아님): 미리 채운 단지만 검색 시 즉답.

## 전제
- `claude` CLI가 구독 인증된 상태(headless `claude -p`가 동작).
- DB에 단지 적재돼 있어야 함(C9/C10 ingest). enrichment store(P1-1)·로더(C4/C5)·`_bootstrap`(C12).
- `auto_enrich.py`는 `_bootstrap`으로 sys.path를 처리 → `PYTHONPATH` 불필요.

## 수동 실행
```bash
make auto-enrich ATTR=gym LIMIT=20         # gym 20단지
make auto-enrich ATTR=pet LIMIT=20         # pet 20단지
make auto-enrich ATTR=both LIMIT=30        # 둘 다
# 또는 직접:
cd apps/api && uv run python scripts/auto_enrich.py --attribute gym --limit 20 --max-turns 60
```

## cron 예시 (야간 저volume·누적)
```cron
# 매일 03:10 gym 30단지, 03:40 pet 30단지 — 구독 rate 보호 위해 소량·시차.
10 3 * * *  cd /path/to/ht-estate && make auto-enrich ATTR=gym LIMIT=30 >> /var/log/ht-enrich.log 2>&1
40 3 * * *  cd /path/to/ht-estate && make auto-enrich ATTR=pet LIMIT=30 >> /var/log/ht-enrich.log 2>&1
```
미적재 단지만 선택(`has_fresh` skip)하므로 매일 돌면 커버리지가 **점진 누적**된다. 전국 ~22k는
관심 지역(세대수 큰 단지 우선)부터 시간 두고 채운다 — lazy 설계라 전수 불필요.

## 안전장치 (무검토 자동화 — 정직한 한계)
인터랙티브 (a)는 매 배치 사람/Web이 검수했으나 cron은 **무인**이다. 따라서:
1. **보수적 프롬프트** — 애매하면 `unknown`(`scripts/prompts/enrich_{gym,pet}.md`에 규율 인코딩:
   단지내-vs-상업 구별 · no-signal→unknown · 차단도메인 금지 · 약한출처 단독 yes 금지 · 오귀속 배제).
2. **파서가 규율 강제**(`parse_output`) — 프롬프트를 어겨도 코드가 막는다:
   차단도메인(naver/hogangnono/asil) source drop · 상태 도메인 밖→unknown 강등 · 환각 단지(요청 후보 밖) drop ·
   source_url/evidence 없으면 drop · pet `confirm_with_office` 전수 true 강제.
3. **출처 전수 기록** — 모든 fact가 `source_url`(http 또는 `urn:ht-estate:auto:<id>`)을 들고 감 → 감사 가능.
4. **주기적 spot-audit 권장** — C4/C5/C7처럼 샘플 `source_url`을 사람이 web_fetch로 검증. 특히 `yes`/`conditional`.
5. **시드 자동 commit 안 함** — auto_enrich는 `data/seeds/*.jsonl`에 **append만**(로컬). 사람이 리뷰 후 commit.
   적재 DB는 gitignore. → 무인 agent 출력이 자동으로 main에 들어가지 않는다.

## 한계
- **pet 천장**(§11): 자동화해도 공개 per-complex 반려동물 정책이 없는 단지는 `unknown`. gym·후기엔 효과 큼.
- **무검토 품질**: spot-audit로 보완. 라이브 첫 배치는 특히 샘플 검증 권장.
- **rate**: 구독 한도라 run당 소량(20~50). 대량 일괄 금지.
