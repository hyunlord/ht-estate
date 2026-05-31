"""실거래↔단지 퍼지 조인 백필 (설계 §5.1).

transaction.complex_id가 NULL인 행을 법정동으로 후보를 좁혀 단지명 유사도로 매칭,
complex_id·match_confidence를 채운다. **조인 컬럼만 갱신**(apt_name_raw 등 불변).
멱등: 이미 매칭(complex_id NOT NULL)된 행은 건드리지 않아 재실행 결과가 같다.
T0-3 upsert가 재적재 시 조인 컬럼을 보존하므로 적재와 백필은 독립이다.

aptSeq↔kaptCode 직접 링크는 불가(라이브 확정 — 다른 ID 체계)라 퍼지가 유일 경로.
narrowing은 **법정동코드(bjd_code) 동등**을 우선한다(T0-4b). bjd_code 없는 행만
동 이름으로 fallback.

**지번 매칭(T0-4c)** — 이름 path가 무매치일 때만 추가로 시도하는 회수 경로다(이름 path는
불변 → 기존 매칭·정밀도 그대로). 같은 narrowing 그룹에서 같은 캐논 지번을 가진 단지를 찾아:
- 0개 → 무매치(구조적 손실: K-apt에 해당 지번 단지 없음).
- 1개(단일 점유) → 이름 타당성 `JIBUN_NAME_FLOOR` 이상이면 매칭. floor는 **알려진 오매칭
  (청담대림이편한세상→청담대림, 유사도 0.615)을 배제**하도록 0.70으로 둔다 — 이름 유사도만으로는
  진짜 회수(지역 prefix)와 이 오매칭을 가르지 못해(0.5~0.62 구간이 겹침) 정밀도 우선으로
  높게 잡는다. floor 미만의 모호 구간은 NULL로 남기고 손실분해에 보고한다.
- 2개 이상(지번 충돌) → 이름 임계+모호갭으로 disambiguate, 모호하면 NULL.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from app.match.fuzzy import (
    DEFAULT_AMBIGUITY_GAP,
    DEFAULT_THRESHOLD,
    best_match,
    similarity,
)
from app.match.jibun import from_kapt_address, to_canonical
from app.match.normalize import extract_dong

# 지번 회수 경로 정책 상수.
JIBUN_NAME_FLOOR = 0.70  # 단일 점유 지번 회수의 이름 타당성 하한(오매칭 0.615 배제)
JIBUN_MATCH_CONFIDENCE = 0.9  # 지번+법정동 단일 점유 매칭 신뢰도(이름퍼지 0.85 < · < 도로 0.95)

# 퍼지조인 백필 대상 테이블 — 매매·전월세 조인 컬럼 동형(P2-1). f-string SQL 주입 방지 allowlist.
JOINABLE_TABLES = {"transaction", "rent_transaction"}

# (complex_id, name) 후보 한 건.
Candidate = tuple[str, str]


def _complex_dong(row: sqlite3.Row) -> str | None:
    """단지 행의 법정동. dong 컬럼 우선, 없으면 legal_addr에서 추출."""
    return row["dong"] or extract_dong(row["legal_addr"])


class _Indexes:
    """narrowing 인덱스 묶음 — 단지 1패스로 구성, backfill/breakdown이 공유한다."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.by_bjd: dict[str, list[Candidate]] = defaultdict(list)
        self.by_dong: dict[str, list[Candidate]] = defaultdict(list)
        # complex_id → 캐논 지번(legal_addr 파싱). 지번 회수 경로의 키.
        self.complex_jibun: dict[str, str] = {}
        for c in conn.execute(
            "SELECT complex_id, name, bjd_code, dong, legal_addr FROM complex"
        ):
            if not c["name"]:
                continue
            entry: Candidate = (c["complex_id"], c["name"])
            if c["bjd_code"]:
                self.by_bjd[c["bjd_code"]].append(entry)
            dong = _complex_dong(c)
            if dong:
                self.by_dong[dong].append(entry)
            jibun = to_canonical(from_kapt_address(c["legal_addr"]))
            if jibun:
                self.complex_jibun[c["complex_id"]] = jibun

    def candidates(self, txn: sqlite3.Row) -> list[Candidate] | None:
        """거래의 narrowing 후보. bjd_code 우선, 없으면 동 이름. 키 자체가 없으면 None."""
        if txn["bjd_code"]:
            return self.by_bjd.get(txn["bjd_code"], [])
        if txn["legal_dong"]:
            return self.by_dong.get(txn["legal_dong"], [])
        return None

    def jibun_peers(self, txn: sqlite3.Row, candidates: list[Candidate]) -> list[Candidate]:
        """narrowing 후보 중 거래와 같은 캐논 지번을 가진 단지들."""
        tj = txn["jibun"]
        if not tj:
            return []
        return [c for c in candidates if self.complex_jibun.get(c[0]) == tj]


def _jibun_match(
    txn: sqlite3.Row,
    candidates: list[Candidate],
    idx: _Indexes,
    *,
    threshold: float,
    ambiguity_gap: float,
) -> tuple[str, float] | None:
    """이름 path 무매치 시 지번으로 회수. (complex_id, confidence) 또는 None.

    단일 점유 지번은 이름 타당성 floor로, 충돌 지번은 이름 임계+모호갭으로 거른다.
    """
    peers = idx.jibun_peers(txn, candidates)
    if not peers:
        return None
    if len(peers) == 1:
        complex_id, cand_name = peers[0]
        if similarity(txn["apt_name_raw"], cand_name) >= JIBUN_NAME_FLOOR:
            return complex_id, JIBUN_MATCH_CONFIDENCE
        return None
    # 지번 충돌 — 같은 지번 단지들 사이에서 이름으로 disambiguate(임계+모호갭).
    return best_match(
        txn["apt_name_raw"], peers, threshold=threshold, ambiguity_gap=ambiguity_gap
    )


def backfill_matches(
    conn: sqlite3.Connection,
    *,
    table: str = "transaction",
    threshold: float = DEFAULT_THRESHOLD,
    ambiguity_gap: float = DEFAULT_AMBIGUITY_GAP,
    use_jibun: bool = True,
) -> dict[str, int]:
    """complex_id NULL인 거래를 일괄 매칭. {matched, unmatched, total} 반환. 멱등.

    무매치/모호는 complex_id를 NULL로 남긴다(억지매칭 금지). 매칭된 행에만
    match_confidence를 채운다. `use_jibun=False`면 이름 path만(T0-4b 베이스라인 — 회수 전후 비교용).
    `table`은 매매("transaction") 기본 — 전월세("rent_transaction")도 동형 컬럼이라 재사용(P2-1).
    """
    if table not in JOINABLE_TABLES:
        raise ValueError(f"조인 불가 테이블: {table} (가능: {sorted(JOINABLE_TABLES)})")
    idx = _Indexes(conn)
    pending = conn.execute(
        f'SELECT txn_id, apt_name_raw, legal_dong, bjd_code, jibun FROM "{table}" '
        "WHERE complex_id IS NULL"
    ).fetchall()

    matched = 0
    for txn in pending:
        if not txn["apt_name_raw"]:
            continue
        candidates = idx.candidates(txn)
        if candidates is None:
            continue
        # 이름 path 우선(불변). 무매치면 지번 회수 경로.
        result = best_match(
            txn["apt_name_raw"], candidates, threshold=threshold, ambiguity_gap=ambiguity_gap
        )
        if result is None and use_jibun:
            result = _jibun_match(
                txn, candidates, idx, threshold=threshold, ambiguity_gap=ambiguity_gap
            )
        if result is None:
            continue
        complex_id, score = result
        conn.execute(
            f'UPDATE "{table}" SET complex_id = ?, match_confidence = ? WHERE txn_id = ?',
            (complex_id, score, txn["txn_id"]),
        )
        matched += 1

    conn.commit()
    return {"matched": matched, "unmatched": len(pending) - matched, "total": len(pending)}


def recall_breakdown(
    conn: sqlite3.Connection,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    ambiguity_gap: float = DEFAULT_AMBIGUITY_GAP,
) -> dict[str, int]:
    """현재 미매치(complex_id NULL) 거래를 손실 유형별로 분해. "더 짜낼 게 있나"의 답.

    이름만 매칭한 상태에서 호출하면 잔여 손실을 회수 가능/구조적으로 가른다:
    - jibun_recoverable: 지번 회수 경로가 실제로 잡을 거래(단일 점유+이름 타당, 또는 충돌이나
      이름으로 disambiguate 성공). use_jibun=True의 추가 매칭 수와 일치한다.
    - name_blocked   : 같은 지번 단지가 단 하나 있으나 이름 타당성(floor) 미달 — 정밀도 가드로
      회수 포기(예: 청담대림 오매칭 패턴). 구조적은 아니나 안전하게 못 메운다.
    - collision      : 지번 충돌(다단지)이라 이름으로도 못 가른 거래(모호).
    - structural     : K-apt에 해당 지번 단지가 없음(매칭으로 못 메움 — 천장).
    - no_jibun       : 거래 지번 자체가 없음(파싱 불가/구 데이터).
    """
    idx = _Indexes(conn)
    buckets = {
        "jibun_recoverable": 0,
        "name_blocked": 0,
        "collision": 0,
        "structural": 0,
        "no_jibun": 0,
        "total_unmatched": 0,
    }
    pending = conn.execute(
        'SELECT txn_id, apt_name_raw, legal_dong, bjd_code, jibun FROM "transaction" '
        "WHERE complex_id IS NULL"
    ).fetchall()
    buckets["total_unmatched"] = len(pending)

    for txn in pending:
        candidates = idx.candidates(txn)
        if candidates is None or not txn["apt_name_raw"]:
            buckets["structural"] += 1
            continue
        if not txn["jibun"]:
            buckets["no_jibun"] += 1
            continue
        peers = idx.jibun_peers(txn, candidates)
        if not peers:
            buckets["structural"] += 1
        elif _jibun_match(
            txn, candidates, idx, threshold=threshold, ambiguity_gap=ambiguity_gap
        ) is not None:
            buckets["jibun_recoverable"] += 1
        elif len(peers) == 1:
            buckets["name_blocked"] += 1  # 단일 점유지만 이름 타당성 미달(정밀도 가드)
        else:
            buckets["collision"] += 1
    return buckets
