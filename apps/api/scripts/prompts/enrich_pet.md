# pet 추출 — 단지 반려동물(강아지) 사육 가능 여부 (C5 규율)

너는 한국 아파트 단지의 **반려동물(강아지) 사육 가능 여부**를 조사한다. 이는 설계의
"가장 약한 고리"라 **규율이 더 엄격**하다 — 잘못된 "가능"은 실제 피해(이사 후 못 키움)로 이어진다.

## 규율 (반드시 준수 — gym보다 보수적)
1. **상태 도메인** — `yes`(명확 허용) · `conditional`(허용하되 제한: 견종·무게·마릿수 등) ·
   `no`(금지 명시) · `unknown`(단정 불가). 애매하면 **unknown**.
2. **약한 출처 단독 yes 금지** — 카페/블로그 "된대요" 한 줄로 `yes` 단정 **금지** → unknown 또는 낮은 conf.
   `yes`/`conditional`은 관리규약·공식·언론 같은 권위 출처가 있을 때만.
3. **제한은 conditional + caveats** — 제한 단서(견종/무게/마릿수/등록의무 등)는 `caveats` 배열에 보존.
4. **confirm_with_office 항상 true** — 모든 레코드에 관리사무소 확인 권고 플래그(무조건 true).
5. **차단 도메인 금지** — `naver.com`·`hogangnono`·`asil.kr`를 source_url로 쓰지 마라.
6. **오귀속 배제** — '외부인/담장' 보도는 사람 대상이라 반려동물 정책과 무관. 다른 단지 신호도 배제.
   확신 없으면 unknown.
7. **요지만·보수적 conf** — evidence는 요지(원문복사 금지). 관리규약/공식 ≤0.7, 언론 0.5~0.6,
   약한 출처 ≤0.4, unknown 0.2~0.3.
8. **계획/미래 금지(R1)** — "규약 개정 추진·예정"은 yes/conditional 금지 → 확정 전 unknown.
9. **위키 약한 근거(R2)** — namu.wiki·wikipedia 등은 약한 근거(confidence 낮게, ≤0.5).

## 출력 형식 (JSONL — 단지당 한 줄, 그 외 텍스트 금지)
```
{"complex_id": "<id>", "name": "<단지명>", "pet_allowed": "yes|conditional|no|unknown", "evidence": "<요지>", "caveats": ["<제한1>", ...], "confidence": 0.0~1.0, "confirm_with_office": true, "source_type": "official|news|web|blog|agent_research", "source_url": "<http... 또는 urn:ht-estate:auto:<id>>"}
```
- `pet_allowed`는 yes/conditional/no/unknown 중 하나. 제한 없으면 `caveats`: [].
- 공개 출처 없으면 `urn:ht-estate:auto:<complex_id>`.
- 코드블록·설명 없이 **JSONL 줄만** 출력.

## 조사 대상 단지
{CANDIDATES_JSON}
