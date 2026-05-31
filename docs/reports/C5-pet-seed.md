# 리턴팩 C5 — 강아지(pet_allowed) 시드 부트스트랩

## 변경 요약
gym(C4)과 같은 (a) 경로로 강남권(강남3구) 10개 단지의 `pet_allowed`를 CC 웹검색으로 추출해
정적 시드(`data/seeds/pet_gangnam.jsonl`)에 적재. pet은 설계의 "가장 약한 고리"(§6·§11)라
규율을 더 엄격히 적용: 보수적 confidence · `confirm_with_office` 전수 true · 견종/제한 caveats ·
잘못된 "가능" 금지 · 차단도메인(naver/hogangnono/asil) 출처 0. 로더는 C4 `load_gym_seed`를
공용 코어(`_seedlib`)로 **속성 일반화**하고 pet 전용 로더(`load_pet_seed`)를 추가 — gym 회귀 0.

## 커버리지 / 단지 선정
- **kaptCode 출처 = 검증된 것만**: R1 K-apt 프로브(`scripts/r1_kapt_probe.json`, K-apt API로 확인된 17단지 중 강남3구 8) + C4 gym 시드에서 CC가 확인한 2(퍼스티어·개포자이) = **10단지**. kaptCode 날조는 가장 심각한 규율 위반이므로 검증된 코드만 사용(템플릿의 "20~40"보다 적음 — 남은 수는 K-apt 적재 후 확대).

| complex_id | 단지 | 구 | pet_allowed | conf | 출처 |
|---|---|---|---|---|---|
| A10023348 | 개포자이프레지던스 | 강남 | **conditional** | 0.55 | news (mk.co.kr) |
| A10022859 | 디에이치퍼스티어아이파크 | 강남 | unknown | 0.2 | agent_research (urn) |
| A10027474 | 역삼자이아파트 | 강남 | unknown | 0.2 | agent_research (urn) |
| A10027800 | 래미안대치팰리스 | 강남 | unknown | 0.2 | agent_research (urn) |
| A10025203 | 디에이치아너힐즈 | 강남 | unknown | 0.2 | agent_research (urn) |
| A13583507 | 은마 | 강남 | unknown | 0.2 | agent_research (urn) |
| A10027205 | 아크로리버파크 | 서초 | unknown | 0.2 | agent_research (urn) |
| A10026004 | 신반포자이아파트 | 서초 | unknown | 0.2 | agent_research (urn) |
| A10025850 | 헬리오시티아파트 | 송파 | unknown | 0.2 | agent_research (urn) |
| A13822004 | 잠실엘스아파트 | 송파 | unknown | 0.2 | agent_research (urn) |

**분포: conditional 1 · unknown 9.** 이 편중이 곧 §11 "가장 약한 고리"의 실증 — 단지 내부
반려동물 정책의 공개·비차단·권위 출처는 매우 드물다. 약한 신호를 yes로 올리지 않는 것이 규율.

## 규율 적용 (채점 기준)
- **보수적 confidence**: conditional 0.55(news, 관리규약 자체 아님), unknown 0.2. 카페/blog 단독 yes 0건.
- **conditional로 제한 포착**: 개포자이 — caveats `["입주민 반려동물 인식표(등록) 착용 의무", "외부 반려동물 단지 출입 제한"]`.
- **unknown demote**: 신호 없음/노이즈(동물병원·상가·매트광고·타단지)는 전부 unknown.
- **오귀속 배제**: '강아지 샤워장 결사반대' 기사는 fetch로 확인 결과 **둔촌주공(올림픽파크포레온)** 건 → 퍼스티어에 귀속하지 않고 unknown 유지. (misattribution 함정 회피, evidence에 명시)
- **관리사무소 확인 플래그**: 10/10 `confirm_with_office: true`. 로더는 누락 시에도 보수적으로 true.
- **차단도메인 출처 0**: naver/hogangnono/asil 0건(검색 시 제외 + 시드 미사용). 원문 복사 없음(evidence는 요지).

## 샘플 fact (출처 포함)
- **개포자이프레지던스** (conditional, conf 0.55, news):
  `{"pet_allowed":"conditional","evidence":"매일경제 2024-06 보도: 입주민 반려동물에 한해 인식표를 발급(유가)하고 미착용 시 외부 이동 조치, 외부인 반려동물의 단지 출입은 제한…","caveats":["입주민 반려동물 인식표(등록) 착용 의무","외부 반려동물 단지 출입 제한"],"confirm_with_office":true}`
  출처: https://www.mk.co.kr/news/realestate/11033039 (fetch로 강남구 개포자이 프레지던스 건 확인)
- **퍼스티어** (unknown, conf 0.2, agent_research):
  evidence: "공식홈·관리규약·언론에서 단지 내부 반려동물 정책 확인 불가. '강아지 샤워장' 기사는 둔촌주공 건이라 무관 — 오귀속 배제". 출처: `urn:ht-estate:c5-agent:A10022859`

## 로더 (일반화)
- `scripts/_seedlib.py` (신규) — 공용 코어: `read_records(path)` + `load_seed(conn, records, *, attribute, to_fact, ttl, now)`. 멱등(write_facts upsert)·재개(has_fresh skip).
- `scripts/load_gym_seed.py` (리팩터) — 코어 위임. 공개 심볼(`ATTRIBUTE`·`load_seed`·`load_seed_records`·`main`) 유지 → C4 테스트 그대로 통과(회귀 0).
- `scripts/load_pet_seed.py` (신규) — `attribute="pet_allowed"`, value=`{pet_allowed, evidence, caveats, confirm_with_office}`.
- 실행: `uv run python scripts/load_pet_seed.py` (complex FK 선적재 가정).

## 객관 게이트 (raw)
- ruff: `All checks passed!`
- pyright: `0 errors, 0 warnings, 0 informations`
- pytest: `181 passed` (신규 pet 로더 5건 + 기존 gym 로더 4건 그대로 통과 = 회귀 0)
- e2e demo(실 시드, complex 선적재): 적재 `{loaded:10, skipped:0, complexes:10}` · 멱등 재실행 `{loaded:0, skipped:10}` · confirm_with_office 10/10 true · 차단도메인 0.

## 미해결 / 결정필요
- **커버리지**: 검증 kaptCode 10단지만(템플릿 20~40 미달). 확대하려면 K-apt 적재(T0-2)로 더 많은 강남 단지의 kaptCode 확보가 선행. 코드 날조 금지 원칙상 본 배치는 10으로 한정.
- **분포 편중(unknown 9)**: 공개 출처 한계의 실증. 라이브 추출(P1-3-live, 키)이나 관리규약 직접 입수가 있어야 conditional/yes 비율이 오른다.

## 다음 제안
- **P1-3 pet 카드** — gym 카드(P1-2b) 패턴 재사용: ✓/conditional(△+caveats)/✗/unknown(△)/none + **관리사무소 확인 권고 배지** + 출처 딥링크. `confirm_with_office`·`caveats`를 UI로.
- **C5 후속 배치** — K-apt 적재 후 강남 단지 kaptCode 확보 → pet 추가 추출.
- **gym soft 랭킹** — 후보를 gym/pet enrichment 점수로 정렬(설계 §7).
