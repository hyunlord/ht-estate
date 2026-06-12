"""자연어 질의 → 일반화 spec 파서 (P4-2b).

자연어를 #2a **조건 레지스트리(criteria.REGISTRY)를 어휘로** 삼아 HardFilterSpec(+ soft)에
매핑한다. "역세권 신축 어린이집 있는 큰 단지, 강아지 되면 좋고" → hard/soft 분류 + 감지 표면화.

레이어 분리:
- **추출(claude -p)**: ClaudeRunner가 NL+레지스트리 카탈로그 프롬프트를 던져 JSON을 받는다(구독
  인증, 키 불필요). 웹 도구 불필요 — 순수 텍스트→JSON. 테스트는 runner를 mock으로 주입(키리스).
- **검증(결정론·키리스)**: LLM JSON을 레지스트리에 grounding — 미등록 soft key·환각 hard
  필드명은 drop, 매핑 불가 구절은 `unsupported` 표면화(발명 금지). min>max는 QueryParseError.
- **감지(결정론)**: `detected`는 **확정 spec에서 역산**(어떤 조건을 hard/soft로 반영했나) —
  LLM이 준 구절(phrase)로 주석만 보강. spec과 항상 정합(#3 "감지·반영" 칩 재료).

핵심 불변식 **demote-not-exclude**: 모호한 NL은 프롬프트가 soft로 분류 → 후보 SET을 떨구지 않는다.
"""

from __future__ import annotations

import json
import subprocess
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from app.search.criteria import REGISTRY
from app.search.spec import HardFilterSpec, SoftSpec

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
PARSE_PROMPT = "parse_query.md"

# 주입형 claude 러너 — (prompt, max_turns) → stdout. 테스트는 mock 주입(auto_enrich와 동형).
ClaudeRunner = Callable[[str, int], str]

_PREFS = ("required", "preferred", "none")

# HardFilterSpec 필드 → 레지스트리 key 역산(감지 표면화·grounding). hard_able만 hard_fields 보유.
_FIELD_TO_KEY: dict[str, str] = {
    field: crit.key for crit in REGISTRY.values() for field in crit.hard_fields
}

# 레지스트리 밖 core hard 필드 → 감지 key·라벨(거래·면적·거래유형·지역 — #2a 조건 아니나 반영).
_CORE_FIELDS: dict[str, str] = {
    "net_area_min": "net_area",
    "net_area_max": "net_area",
    "price_min": "price",
    "price_max": "price",
    "deposit_min": "deposit",
    "deposit_max": "deposit",
    "monthly_rent_min": "monthly_rent",
    "monthly_rent_max": "monthly_rent",
    # school-assignment: 배정 초등 categorical 하드(registry criterion 아님 — net_area 류).
    "assigned_school": "assigned_school",
}
_CORE_LABELS: dict[str, str] = {
    "net_area": "전용면적",
    "price": "매매가",
    "deposit": "보증금",
    "monthly_rent": "월세",
    "deal_type": "거래유형",
    "region": "지역(지도범위)",
    "assigned_school": "배정 초등(통학구역)",
}


class QueryParseError(ValueError):
    """LLM 출력을 spec으로 파싱 불가(빈 응답·JSON 아님·모순 범위). 엔드포인트 422로 매핑."""


class Detected(BaseModel):
    """감지·반영 한 건 — 어떤 NL 구절을 어떤 조건으로 hard/soft 반영했는지(#3 칩·튜닝 재료)."""

    criterion_key: str
    label: str
    mode: Literal["hard", "soft"]
    phrase: str | None = None


class ParsedQuery(BaseModel):
    """NL 파싱 결과 — 확정 spec + 감지 + 매핑 불가(unsupported) + 평판 의도(reputation_query).

    reputation_query: 구조 필드에 매핑 안 되는 **주관 평판 의도**("관리 잘 되는·조용한") free-text.
    드롭(unsupported) 대신 추출 → 프론트가 detail 평판 섹션(E3 RAG)에 pre-seed. 없으면 None.
    """

    spec: HardFilterSpec
    detected: list[Detected]
    unsupported: list[str]
    reputation_query: str | None = None


def _default_runner(prompt: str, max_turns: int) -> str:
    """`claude -p`(headless·구독 인증). NL 파싱은 웹 불필요 — 도구 미승인. 키 불필요."""
    proc = subprocess.run(
        ["claude", "-p", prompt, "--max-turns", str(max_turns)],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout


def registry_catalog() -> str:
    """REGISTRY를 프롬프트 어휘로 렌더 — 등록 조건이 곧 파서 사전(새 조건 자동 반영, 드리프트 0)."""
    lines: list[str] = []
    for crit in REGISTRY.values():
        modes: list[str] = []
        if crit.hard_able:
            modes.append(f"hard 필드={'·'.join(crit.hard_fields)}")
        if crit.soft_able:
            modes.append("soft 가능")
        suffix = f", 값={'|'.join(crit.values)}" if crit.values else ""  # enum 노출 — P5-1
        lines.append(
            f"- `{crit.key}` ({crit.label}) — type={crit.value_type}, {', '.join(modes)}{suffix}"
        )
    return "\n".join(lines)


def build_parse_prompt(nl: str) -> str:
    """프롬프트 템플릿에 레지스트리 카탈로그·질의 치환."""
    template = (PROMPTS_DIR / PARSE_PROMPT).read_text(encoding="utf-8")
    return template.replace("{REGISTRY_CATALOG}", registry_catalog()).replace("{QUERY}", nl)


def _extract_json_object(text: str) -> dict[str, object] | None:
    """모델 출력에서 첫 균형 JSON 객체를 추출(코드펜스·잡설·줄바꿈 관용). 없으면 None.

    문자열 리터럴 안의 중괄호는 무시하며 brace 매칭 → 다중 줄 중첩 객체도 안전.
    """
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break  # 깨진 객체 — 다음 '{'부터 재시도
                    return obj if isinstance(obj, dict) else None
        start = text.find("{", start + 1)
    return None


def _grounded_hard(hard_raw: object) -> dict[str, object]:
    """LLM hard 딕트 → 알려진 HardFilterSpec 필드만(환각 필드명·soft 키 drop)."""
    if not isinstance(hard_raw, dict):
        return {}
    known = set(HardFilterSpec.model_fields) - {"soft"}
    return {k: v for k, v in hard_raw.items() if k in known}


def _grounded_soft(soft_raw: object) -> SoftSpec:
    """LLM soft 딕트 → SoftSpec — gym/pet preference 검증, criteria는 soft-able만(환각 drop)."""
    if not isinstance(soft_raw, dict):
        return SoftSpec()
    gym = soft_raw.get("gym")
    pet = soft_raw.get("pet")
    kept: list[dict[str, object]] = []
    raw_criteria = soft_raw.get("criteria")
    if isinstance(raw_criteria, list):
        for c in raw_criteria:
            if not isinstance(c, dict):
                continue
            key = c.get("key")
            crit = REGISTRY.get(key) if isinstance(key, str) else None
            if crit is None or not crit.soft_able:
                continue  # 미등록·하드전용 → 환각 drop
            try:
                weight = float(c.get("weight", 1.0))
            except (TypeError, ValueError):
                weight = 1.0
            kept.append({"key": key, "weight": max(0.0, weight)})
    # model_validate로 구성(임의 LLM 딕트는 object 타입 — 명시 검증 경로가 타입 안전).
    return SoftSpec.model_validate(
        {
            "gym": gym if gym in _PREFS else "none",
            "pet": pet if pet in _PREFS else "none",
            "criteria": kept,
        }
    )


def _derive_detected(spec: HardFilterSpec, llm_detected: object) -> list[Detected]:
    """확정 spec에서 감지 역산(결정론) — LLM phrase로 주석 보강. spec과 항상 정합."""
    phrases: dict[str, str] = {}
    if isinstance(llm_detected, list):
        for d in llm_detected:
            if not isinstance(d, dict):
                continue
            key = d.get("criterion_key")
            phrase = d.get("phrase")
            if isinstance(key, str) and phrase:
                phrases.setdefault(key, str(phrase))

    out: dict[tuple[str, str], Detected] = {}
    dump = spec.model_dump()

    # 레지스트리 hard 조건(필드 set됨)
    for field, key in _FIELD_TO_KEY.items():
        if dump.get(field) is None:
            continue
        out[(key, "hard")] = Detected(
            criterion_key=key, label=REGISTRY[key].label, mode="hard", phrase=phrases.get(key)
        )
    # core hard 필드(거래·면적)
    for field, key in _CORE_FIELDS.items():
        if dump.get(field) is None:
            continue
        out[(key, "hard")] = Detected(
            criterion_key=key, label=_CORE_LABELS[key], mode="hard", phrase=phrases.get(key)
        )
    if spec.deal_type != "sale":
        out[("deal_type", "hard")] = Detected(
            criterion_key="deal_type", label=_CORE_LABELS["deal_type"], mode="hard",
            phrase=phrases.get("deal_type"),
        )
    if spec.has_bbox:
        out[("region", "hard")] = Detected(
            criterion_key="region", label=_CORE_LABELS["region"], mode="hard",
            phrase=phrases.get("region"),
        )
    # soft 활성 조건(gym/pet preference + 일반화 criteria)
    for key, _weight in spec.soft.active_criteria():
        crit = REGISTRY.get(key)
        out[(key, "soft")] = Detected(
            criterion_key=key, label=crit.label if crit else key, mode="soft",
            phrase=phrases.get(key),
        )
    return list(out.values())


def _build_parsed(payload: dict[str, object]) -> ParsedQuery:
    """LLM JSON payload → 검증된 ParsedQuery(grounding·감지 역산·unsupported 표면화)."""
    soft = _grounded_soft(payload.get("soft"))
    hard_fields = _grounded_hard(payload.get("hard"))
    try:
        spec = HardFilterSpec.model_validate({**hard_fields, "soft": soft})
    except ValidationError as exc:
        raise QueryParseError(f"모순/유효하지 않은 spec: {exc}") from exc

    detected = _derive_detected(spec, payload.get("detected"))
    raw_unsup = payload.get("unsupported")
    unsupported = [str(x) for x in raw_unsup] if isinstance(raw_unsup, list) else []
    # 평판 의도(free-text) — 구조 매핑 안 되는 주관 구절. 빈 문자열/비-str은 None(false 라우팅 0).
    rep_raw = payload.get("reputation_query")
    reputation_query = rep_raw.strip() if isinstance(rep_raw, str) and rep_raw.strip() else None
    return ParsedQuery(
        spec=spec, detected=detected, unsupported=unsupported,
        reputation_query=reputation_query,
    )


# nl-fast-parse: 파싱 캐시 — 정규화 쿼리 → ParsedQuery. 파싱은 REGISTRY+지역 어휘에만 의존(거래
# 데이터 무관)이라 단순 바운드 LRU(키=쿼리). 재시작(배포/레지스트리 변경)시 비워짐·ingest 무관.
_PARSE_CACHE: OrderedDict[str, ParsedQuery] = OrderedDict()
_PARSE_CACHE_MAX = 512


def _norm_query(nl: str) -> str:
    return " ".join(nl.lower().split())


def _parse_cache_put(norm: str, parsed: ParsedQuery) -> None:
    _PARSE_CACHE[norm] = parsed
    _PARSE_CACHE.move_to_end(norm)
    while len(_PARSE_CACHE) > _PARSE_CACHE_MAX:
        _PARSE_CACHE.popitem(last=False)


def clear_parse_cache() -> None:
    """파싱 캐시 비우기 — 테스트·수동 무효화용."""
    _PARSE_CACHE.clear()


def parse_query(
    nl: str, *, runner: ClaudeRunner = _default_runner, max_turns: int = 2
) -> ParsedQuery:
    """자연어 질의 → 레지스트리-grounded ParsedQuery(spec + 감지 + unsupported).

    nl-fast-parse: **프로덕션 경로(runner=_default_runner)는 룰 선파싱 + 파싱 캐시 먼저** — 흔한
    쿼리는 claude -p(6-7s) 없이 즉시. 룰이 저신뢰(None)면 LLM 폴백(미파싱 0). 테스트(mock runner)는
    룰/캐시 우회 → 기존 거동·키리스 보존. 룰/LLM 모두 _build_parsed로 동일 grounding·감지 역산.

    runner로 claude -p(구독)에 NL+카탈로그 프롬프트를 던져 JSON을 받고, 결정론 검증으로 grounding.
    빈 응답/JSON 아님/모순 범위 → QueryParseError.
    """
    prod = runner is _default_runner  # 프로덕션 경로에서만 룰/캐시(테스트 mock은 LLM 경로 유지)
    if prod:
        from app.search.rule_parse import try_rule_parse

        norm = _norm_query(nl)
        cached = _PARSE_CACHE.get(norm)
        if cached is not None:
            _PARSE_CACHE.move_to_end(norm)
            return cached
        fast = try_rule_parse(nl)
        if fast is not None:
            _parse_cache_put(norm, fast)
            return fast

    text = runner(build_parse_prompt(nl), max_turns)
    payload = _extract_json_object(text)
    if payload is None:
        raise QueryParseError("모델 출력에서 JSON 객체를 찾지 못함")
    parsed = _build_parsed(payload)
    if prod:
        _parse_cache_put(_norm_query(nl), parsed)
    return parsed
