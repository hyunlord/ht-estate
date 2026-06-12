"""nl-fast-parse: 룰 기반 NL 선파서 — REGISTRY-grounded 결정론 매핑. 흔한 쿼리 LLM(claude -p) 0.

claude -p 폴백은 6-7s(고정 오버헤드 ~3.3s + 생성). 흔한 쿼리는 키워드/숫자/지역으로 결정론 매핑되니
LLM 왕복 없이 <~1ms에 spec을 만든다. 잔여 애매/주관("조용한"·"관리"·미지의 어구)이 남으면 None을
반환 → parse_query가 claude -p로 폴백(**미파싱 0**·기존 거동).

설계:
- LLM과 **동일 어휘**(criteria.REGISTRY)·**동일 검증/감지**(_build_parsed 재사용) → 거동 일관.
- **보수적**: 모호하면 soft(demote-not-exclude)·임계는 명시값일 때만 hard·확신 없으면 폴백.
- reputation_query(주관 평판)는 룰서 만들지 않는다 — 그런 구절이 있으면 잔여로 남아 LLM 폴백.
- 지역명(시군구/동)은 spec 필드가 없다(지도 뷰포트 담당) → 인식해 **소비만**(커버리지)·spec 무영향.
- **read-only**(canon write 0)·순수 함수(DB 무의존) → 지문/counts 자명 불변.
"""

from __future__ import annotations

import re

# 평↔㎡ (format.ts SQM_PER_PYEONG와 동일).
_SQM_PER_PYEONG = 3.3058


class _Acc:
    """룰 누적기 — hard 필드·soft criteria·gym/pet pref. residual에서 매칭 스팬을 지운다."""

    def __init__(self, text: str) -> None:
        self.residual = text
        self.hard: dict[str, object] = {}
        self.soft_criteria: set[str] = set()
        self.gym = "none"
        self.pet = "none"
        self.signals = 0  # hard/soft/gym/pet/deal_type 신호 수(룰 사용 confidence 게이트)

    def consume(self, span: re.Match[str]) -> None:
        # 매칭 스팬을 공백으로 치환(중복 매칭·잔여 계산서 제외).
        self.residual = self.residual[: span.start()] + " " + self.residual[span.end() :]


# ── soft criteria(비교형·"가까운/많은/넓은/큰/면 좋고") → REGISTRY soft key ──
# 패턴은 길고 구체적인 것 우선. 값/임계 없는 비교형은 전부 soft(demote-not-exclude·보수적).
# 비교형 동사/형용사는 어미를 [가-힣]*로 흡수("가까운"→가까+운, "넉넉한"→넉넉+한·잔여 0).
_SOFT_RULES: tuple[tuple[str, str], ...] = (
    (r"초역세권|역세권|지하철|전철|역\s*(?:가까|근처|인접)[가-힣]*", "subway_time"),
    (r"신축|새\s*아파트|준신축|신축급|새것|연식\s*짧[가-힣]*", "approval_year"),
    (r"대단지|큰\s*단지|대규모|세대\s*수?\s*많[가-힣]*|넓은|큰\s*평형?|대형", "household_count"),
    (r"엘[리베]*베이?터?|승강기", "elevator_count"),
    (r"cctv|씨씨티비|시시티비|보안\s*카메라", "cctv_count"),
    (r"주차\s*(?:넉넉|많|좋|편|여유)[가-힣]*", "parking_ratio"),
    (r"초등(?:학교)?\s*(?:가까|근처|인접)[가-힣]*|초품아", "elem_dist"),
    (r"중학교?\s*(?:가까|근처|인접)[가-힣]*", "mid_dist"),
    (r"고등학교?\s*(?:가까|근처|인접)[가-힣]*", "high_dist"),
    (r"대형\s*마트|마트", "mart"),
    (r"편의점", "conv"),
    (r"병원", "hospital"),
    (r"약국", "pharmacy"),
    (r"공원", "park"),
)

# ── gym/pet(state pref·soft 전용) ──
_GYM_RE = re.compile(r"헬스(?:장)?|피트니스|피트니스센터|짐|gym|헬스클럽|운동\s*시설")
_PET_RE = re.compile(r"강아지|반려\s*동물|반려견|애견|반려묘|반려|펫|고양이")

# ── 지역명(시군구/동) — spec 필드 없음(지도 담당). 인식해 소비만(커버리지). 접미(구/시/군/동) +
# 접미 없는 흔한 광역/구 명 일부. 미인식 지역은 잔여로 남아 폴백(안전). ──
_REGION_SUFFIX_RE = re.compile(
    r"[가-힣]{2,4}(?:특별시|광역시|특별자치시|특별자치도|시|군|구|동|읍|면)"
)
_REGION_BARE: frozenset[str] = frozenset(
    "강남 서초 송파 강동 강서 마포 용산 성동 광진 동작 영등포 양천 구로 금천 관악 종로 중구 "
    "은평 서대문 노원 도봉 강북 성북 중랑 동대문 분당 판교 일산 평촌 산본 중동 광교 송도 위례 "
    "목동 잠실 반포 압구정 청담 대치 도곡 여의도 상암 마곡 위레".split()
)

# ── 거래유형 ──
_DEALTYPE_RES: tuple[tuple[str, str], ...] = (
    (r"전세", "jeonse"),
    (r"월세", "monthly"),
    (r"매매", "sale"),
)

# ── 주택유형(property_type·hard enum) ──
_PROPTYPE_RES: tuple[tuple[str, str], ...] = (
    (r"오피스텔", "officetel"),
    (r"빌라|연립(?:주택)?|다세대", "rowhouse"),
    (r"단독\s*주택|단독", "detached"),
    (r"아파트만|아파트\s*위주", "apartment"),
)

# ── 난방(heat_type·hard exact) ──
_HEAT_RES: tuple[tuple[str, str], ...] = (
    (r"지역\s*난방", "지역난방"),
    (r"개별\s*난방", "개별난방"),
    (r"중앙\s*난방", "중앙난방"),
)

# ── 잔여 불용어(문법조사 + 일반 filler) — **내용어(조용/관리/평판/소음/분위기)는 제외**(폴백 유발).
# ★ 숫자(\d)는 불용어서 제외 — 미소비 숫자는 잔여로 남겨 폴백(silent drop 금지·spec 누락 방지). ──
_STOPWORDS_RE = re.compile(
    r"있는|있고|있으면|있어야|되면|되는|좋고|좋은|좋아|선호|원해|원하는|찾는|찾아|보여|"
    r"곳만|곳|단지|아파트|집|매물|여기|주변|근처|이내|정도|적당|위주|중심|쪽|이상|이하|"
    r"그리고|그리고서|또|또는|및|이랑|랑|와|과|에서|에게|보다|처럼|만큼|한|할|수|좀|꽤|"
    r"많[가-힣]*|가까[가-힣]*|인접[가-힣]*|"  # 명사 규칙이 잡고 남은 비교형 어미(많은·가까운)
    r"[은는이가을를에의도만과와로으로]|[\s,.!?·~/()\-]+"
)

_HARD_MARKER = r"(?:있는|있고|있어야|보유|필수|되는|되어야|만\b|인\s*곳)"


def _apply_numeric(acc: _Acc) -> None:
    """전용/평/㎡·연도·가격(억)·맨숫자(평형) → hard 필드. 단위 붙은 것 먼저(숫자 소비)."""
    # 전용면적: "전용 84"·"전용면적 84"·"84㎡"·"84제곱" → net_area_min(이하면 max)
    for m in re.finditer(r"전용(?:면적)?\s*(\d{2,3})|(\d{2,3})\s*(?:㎡|제곱미?터?)", acc.residual):
        val = float(m.group(1) or m.group(2))
        tail = acc.residual[m.end() : m.end() + 4]
        acc.hard["net_area_max" if "이하" in tail else "net_area_min"] = val
        acc.signals += 1
        acc.consume(m)
    # 평/평대: "84평"·"20평대"(10평 폭 범위) → net_area(㎡ 환산)
    for m in re.finditer(r"(\d{1,3})\s*평(대)?", acc.residual):
        n = int(m.group(1))
        acc.hard["net_area_min"] = round(n * _SQM_PER_PYEONG, 1)
        if m.group(2):  # N평대 → [N, N+10)평
            acc.hard["net_area_max"] = round((n + 10) * _SQM_PER_PYEONG, 1)
        acc.signals += 1
        acc.consume(m)
    # 가격(매매가, 억): "15억 이하" → price_max 150000(만원) · "10억 이상" → price_min
    for m in re.finditer(r"(\d{1,3})\s*억\s*(이하|이상|미만|초과)?", acc.residual):
        manwon = int(m.group(1)) * 10000
        acc.hard["price_max" if m.group(2) in ("이하", "미만") else "price_min"] = manwon
        acc.signals += 1
        acc.consume(m)
    # 연도(4자리 19xx/20xx): "2015년 이후/이상"→approval_year_min · "이전/이하"→max
    for m in re.finditer(r"((?:19|20)\d{2})\s*년?\s*(이후|이상|이전|이하)?", acc.residual):
        year = int(m.group(1))
        before = m.group(2) in ("이전", "이하")
        acc.hard["approval_year_max" if before else "approval_year_min"] = year
        acc.signals += 1
        acc.consume(m)
    # 맨숫자(단위 없음·2~3자리·40~300): 흔한 평형(예 "신축 84"→전용 84㎡). 범위 밖=미소비→폴백.
    if "net_area_min" not in acc.hard and "net_area_max" not in acc.hard:
        m = re.search(r"\b(\d{2,3})\b", acc.residual)
        if m and 40 <= int(m.group(1)) <= 300:
            acc.hard["net_area_min"] = float(m.group(1))
            acc.signals += 1
            acc.consume(m)


def _apply_assigned_school(acc: _Acc) -> None:
    """"○○초 배정/통학구역/배정받는/보내는" → assigned_school(hard). "초등 가까운"[거리]과 구분."""
    m = re.search(r"([가-힣]{2,5}초)(?:등학교)?\s*(?:배정|통학\s*구역|보내)[가-힣]*", acc.residual)
    if m:
        acc.hard["assigned_school"] = m.group(1)
        acc.signals += 1
        acc.consume(m)


def _apply_underground_parking(acc: _Acc) -> None:
    """"지하주차(장)" → parking_underground(hard bool). "주차 넉넉"(soft parking_ratio)과 별개."""
    m = re.search(r"지하\s*주차(?:장)?", acc.residual)
    if m:
        acc.hard["parking_underground"] = True
        acc.signals += 1
        acc.consume(m)


def _apply_daycare(acc: _Acc) -> None:
    """어린이집 — "있는/필수"면 hard has_daycare, 아니면 soft has_daycare(demote-not-exclude)."""
    hard = re.search(rf"(?:단지\s*내\s*)?어린이집\s*{_HARD_MARKER}", acc.residual)
    if hard:
        acc.hard["has_daycare"] = True
        acc.signals += 1
        acc.consume(hard)
        return
    soft = re.search(r"어린이집|유치원", acc.residual)
    if soft:
        acc.soft_criteria.add("has_daycare")
        acc.signals += 1
        acc.consume(soft)


def _build_payload(acc: _Acc) -> dict[str, object]:
    """누적 룰 → _build_parsed가 받는 payload(LLM JSON과 동형)."""
    criteria = [{"key": k, "weight": 1.0} for k in sorted(acc.soft_criteria)]
    return {
        "hard": acc.hard,
        "soft": {"gym": acc.gym, "pet": acc.pet, "criteria": criteria},
        "detected": [],  # _derive_detected가 spec서 역산(LLM 경로와 동일)
        "unsupported": [],
        "reputation_query": None,
    }


def try_rule_parse(nl: str):  # type: ignore[no-untyped-def]  # -> ParsedQuery | None
    """룰로 NL→ParsedQuery. 전체가 고신뢰로 소비되고 ≥1 신호면 반환, 아니면 None(LLM 폴백)."""
    from app.search.nl_parse import _build_parsed  # 순환 import 회피(런타임)

    if not nl or not nl.strip():
        return None
    acc = _Acc(nl.strip())

    # 1) 명시 hard(값/마커 있는 것 — 순서: 구체적 먼저)
    _apply_assigned_school(acc)
    _apply_underground_parking(acc)
    _apply_numeric(acc)
    for pat, value in _HEAT_RES:
        m = re.search(pat, acc.residual)
        if m:
            acc.hard["heat_type"] = value
            acc.signals += 1
            acc.consume(m)
    for pat, value in _PROPTYPE_RES:
        m = re.search(pat, acc.residual)
        if m:
            acc.hard["property_type"] = value
            acc.signals += 1
            acc.consume(m)
    _apply_daycare(acc)

    # 2) 거래유형(매매=기본이라 sale은 신호 카운트 안 함)
    for pat, value in _DEALTYPE_RES:
        m = re.search(pat, acc.residual)
        if m:
            if value != "sale":
                acc.hard["deal_type"] = value
                acc.signals += 1
            acc.consume(m)

    # 3) gym/pet(soft pref)
    gm = _GYM_RE.search(acc.residual)
    if gm:
        acc.gym = "preferred"
        acc.signals += 1
        acc.consume(gm)
    pm = _PET_RE.search(acc.residual)
    if pm:
        acc.pet = "preferred"
        acc.signals += 1
        acc.consume(pm)

    # 4) soft criteria(비교형)
    for pat, key in _SOFT_RULES:
        m = re.search(pat, acc.residual)
        if m:
            acc.soft_criteria.add(key)
            acc.signals += 1
            acc.consume(m)

    # 5) 지역명 소비(spec 무영향 — 커버리지용)
    for m in list(_REGION_SUFFIX_RE.finditer(acc.residual)):
        acc.consume(m)
    for bare in _REGION_BARE:
        idx = acc.residual.find(bare)
        if idx != -1:
            acc.residual = acc.residual[:idx] + " " + acc.residual[idx + len(bare) :]

    # 6) 잔여 = 불용어/조사/숫자/문장부호 제거 후 내용어 남나? 남으면 저신뢰 → 폴백.
    leftover = _STOPWORDS_RE.sub(" ", acc.residual).strip()
    if leftover:
        return None  # 미지/주관 어구 잔존(조용한·관리·평판 등) → LLM 폴백(미파싱 0)
    if acc.signals == 0:
        return None  # 신호 없음(빈 의미·순수 지역) → LLM 경로(빈 spec/실패 판단 위임)

    try:
        return _build_parsed(_build_payload(acc))
    except Exception:  # noqa: BLE001 — grounding 실패(모순 등)면 폴백
        return None
