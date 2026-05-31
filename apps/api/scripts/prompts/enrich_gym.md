# gym 추출 — 단지 내 헬스장/피트니스 여부 (C4 규율)

너는 한국 아파트 단지의 **단지 내(입주민 전용) 헬스장/피트니스** 여부를 조사한다.
아래 단지 목록 각각에 대해 웹을 조사하고 **규율을 지켜** 구조화 결과를 출력한다.

## 규율 (반드시 준수)
1. **단지 내 vs 인근 상업 구별** — 입주민 전용 커뮤니티 피트니스만 `yes`. 단지 밖 상업 헬스장
   (○○피트니스·체인·상가 헬스장)은 단지 시설이 **아니다**. 구별 안 되면 `unknown`.
2. **no-signal → unknown** — 단지 내 시설 정보를 못 찾으면 `no`가 아니라 **`unknown`**.
   `no`는 "단지 내 시설이 없음"이 적극 확인될 때만(예: 구축 재건축 대기 + 시설 부재 확인).
3. **차단 도메인 금지** — `naver.com`·`hogangnono`(호갱노노)·`asil.kr`(아실)을 **source_url로 쓰지 마라**.
   공식홈·언론·KB부동산·건설사 보도자료 등 공개·인용가능 출처만.
4. **no-scrape** — 페이지를 대량 긁지 마라. 공개 검색결과/요약만 본다.
5. **요지만(원문 복사 금지)** — evidence는 1~2문장 요지. 기사/페이지 원문을 그대로 붙이지 마라.
6. **보수적 confidence** — 공식/언론 확인 0.7~0.9, 약한 신호(블로그 단독) ≤0.5, unknown 0.2~0.3.
7. **오귀속 배제** — 검색결과가 **다른 단지**(이름 유사·인접) 것이면 쓰지 마라. 확신 없으면 unknown.

## 출력 형식 (JSONL — 단지당 한 줄, 그 외 텍스트 금지)
각 줄은 아래 키를 가진 JSON 객체:
```
{"complex_id": "<주어진 id>", "name": "<단지명>", "has_gym": "yes|no|unknown", "in_complex": true|false, "evidence": "<요지>", "confidence": 0.0~1.0, "source_type": "official|news|web|blog|agent_research", "source_url": "<http... 또는 urn:ht-estate:auto:<id>>"}
```
- `has_gym`는 반드시 yes/no/unknown 중 하나.
- 공개 출처가 있으면 그 http URL을, 없으면(에이전트 판단) `urn:ht-estate:auto:<complex_id>`.
- 코드블록·설명·머리말 없이 **JSONL 줄만** 출력.

## 조사 대상 단지
{CANDIDATES_JSON}
