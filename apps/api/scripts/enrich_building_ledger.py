"""건축물대장 enrich 러너 (enrich-1) — 비-아파트 빈 속성을 대장 표제부로 벌크채움.

기존 비-아파트 complex_id를 순회 → building_key 파싱(sgg·법정동·지번) → bjdongCd(정적 참조) +
bun/ji 해소 → 표제부 fetch → 건물명 매칭 → enrich_building(UPDATE only·좌표 보존).

규율(refill_kapt_fields와 동형):
- **enrich-only·좌표보존** — ledger_repo가 UPDATE만(INSERT 0 → 건물 수 불변), 컬럼셋에 lat/lng 없음.
- **resume-safe** — 정의적 결과(enriched·no_bjdong·no_jibun·no_match)를 ingest_progress
  (stage='ledger_enrich')에 기록 → 재개 skip. transient(PublicDataError)는 미기록(다음 run 재시도).
- **멱등** — enrich_building이 COALESCE/직접set로 같은 입력=같은 결과.
- **cron-safe** — 공유 `.ingest.lock`을 배치단위 spin-acquire/release(거래 cron과 직렬화·굶김 방지).
- **rate-bound** — `--interval` throttle + `_http` 백오프. `--limit`로 run 바운드(멀티데이).

    uv run python scripts/enrich_building_ledger.py --limit 500          # 이번 run 500건
    uv run python scripts/enrich_building_ledger.py --sido 11 --limit 0  # 서울 전량
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import _bootstrap  # noqa: F401  (apps/api를 sys.path에)
import httpx

# refill과 동일한 cron-lock/배치 메커니즘 재사용(직렬화 동작 일치).
from refill_kapt_fields import DEFAULT_INTER_BATCH_SLEEP, ShlockBatch, _chunks

from app.settings import get_api_key
from app.sources.building_ledger import fetch_title_info, to_bun_ji
from app.sources.errors import PublicDataError
from app.store.db import DEFAULT_DB_PATH, get_connection, init_db
from app.store.ledger_repo import enrich_building, ledger_source_url, pick_match
from app.store.progress_repo import record_month
from app.store.regions import bjdong_code
from app.throttle import Throttle

ENRICH_STAGE = "ledger_enrich"
_ENRICH_MONTH = "-"


def nonapt_targets(
    conn: sqlite3.Connection, sido_prefixes: list[str] | None = None
) -> list[tuple[str, str | None]]:
    """대상 (complex_id, name) — 비-아파트, 결정론 순서. sido_prefixes(sgg 앞2)면 그 시도만."""
    if sido_prefixes:
        ph = ",".join("?" * len(sido_prefixes))
        sql = (
            "SELECT complex_id, name FROM complex WHERE property_type IN ('rowhouse','officetel') "
            f"AND substr(complex_id, 4, 2) IN ({ph}) ORDER BY complex_id"
        )
        rows = conn.execute(sql, list(sido_prefixes)).fetchall()
    else:
        rows = conn.execute(
            "SELECT complex_id, name FROM complex WHERE property_type IN ('rowhouse','officetel') "
            "ORDER BY complex_id"
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


def enriched_ids(conn: sqlite3.Connection) -> set[str]:
    """처리완료 complex_id(enriched + 정의적 skip) — 재개 skip 판정."""
    rows = conn.execute(
        "SELECT region FROM ingest_progress WHERE stage = ?", (ENRICH_STAGE,)
    ).fetchall()
    return {r[0] for r in rows}


def _parse_key(complex_id: str) -> tuple[str, str, str] | None:
    """building_key(pt:sgg:dong:jibun:name) → (sgg_cd, legal_dong, jibun). 형식 불충분이면 None."""
    parts = complex_id.split(":")
    if len(parts) < 5:
        return None
    sgg, dong, jibun = parts[1], parts[2], parts[3]
    if not sgg or sgg == "?" or not dong:
        return None
    return sgg, dong, jibun


def enrich_one(
    conn: sqlite3.Connection,
    complex_id: str,
    name: str | None,
    *,
    api_key: str,
    client: httpx.Client | None = None,
) -> str:
    """한 건물 enrich 시도. 결과: enriched | no_bjdong | no_jibun | no_match(전부 정의적·기록)."""
    parsed = _parse_key(complex_id)
    if parsed is None:
        return "no_jibun"
    sgg, dong, jibun = parsed
    bjdong = bjdong_code(sgg, dong)
    if bjdong is None:
        return "no_bjdong"
    bunji = to_bun_ji(jibun)
    if bunji is None:
        return "no_jibun"
    bun, ji = bunji
    titles = fetch_title_info(sgg, bjdong, bun, ji, api_key=api_key, client=client)
    match = pick_match(titles, name)
    if match is None:
        return "no_match"
    enrich_building(
        conn, complex_id, match, source_url=ledger_source_url(sgg, bjdong, bun, ji)
    )
    return "enriched"


def run_enrich(
    conn: sqlite3.Connection,
    *,
    api_key: str,
    lock: Callable[[], object],
    throttle: Throttle | None,
    batch_size: int,
    limit: int,
    sido_prefixes: list[str] | None = None,
    inter_batch_sleep: float = DEFAULT_INTER_BATCH_SLEEP,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] | None = None,
) -> Counter[str]:
    """미완료 비-아파트를 배치 단위로 enrich. 결과 카운터(enriched/no_*) 반환.

    배치마다 공유락 획득(점유 중이면 양보=중단). 정의적 결과만 레저 기록(재개 skip). 캡 등
    PublicDataError면 우아하게 중단(거래 cron/키 미차단). 좌표는 UPDATE 컬럼셋에 없어 불변.
    """
    done = enriched_ids(conn)
    pending = [(cid, nm) for cid, nm in nonapt_targets(conn, sido_prefixes) if cid not in done]
    if limit > 0:
        pending = pending[:limit]
    if log is not None:
        log(f"enrich 대상 {len(pending)}건 (완료 {len(done)} skip, batch={batch_size})")

    counts: Counter[str] = Counter()
    batches = list(_chunks([cid for cid, _ in pending], batch_size))
    names = dict(pending)
    for bi, batch in enumerate(batches):
        with lock() as acquired:  # type: ignore[attr-defined]
            if not acquired:
                if log is not None:
                    log("공유락 점유 중(cron) — 이번 run 양보, 다음 run 재개")
                break
            for complex_id in batch:
                if throttle is not None:
                    throttle.wait()
                try:
                    outcome = enrich_one(
                        conn, complex_id, names.get(complex_id), api_key=api_key, client=client
                    )
                except PublicDataError as exc:
                    if log is not None:
                        log(
                            f"공공API 오류 code={exc.result_code} ({exc.result_msg}) — "
                            "일일캡/일시 추정, 이번 run 중단(레저로 재개)"
                        )
                    conn.commit()
                    return counts
                except httpx.HTTPError as exc:
                    # 건축HUB 일일쿼터/레이트리밋은 **HTTP 429**로 온다(resultCode 아님). _http가
                    # 8회 백오프 재시도 후에도 남으면 여기로 전파 → 크래시 대신 우아하게 중단(레저로
                    # 재개·완료분 보존). 네트워크 전송오류(재시도 소진)도 동일 — 멱등 이어받음.
                    if log is not None:
                        log(
                            f"HTTP 오류({type(exc).__name__}) — 레이트리밋/쿼터/네트워크 추정, "
                            "이번 run 중단(레저로 재개·완료분 보존)"
                        )
                    conn.commit()
                    return counts
                record_month(conn, ENRICH_STAGE, complex_id, _ENRICH_MONTH, 1)  # 정의적 → 기록
                counts[outcome] += 1
            conn.commit()
        if inter_batch_sleep > 0 and bi < len(batches) - 1:
            sleep(inter_batch_sleep)
        if log is not None and sum(counts.values()) % 200 == 0 and counts:
            log(f"  …처리 {sum(counts.values())}건 (enriched={counts['enriched']})")
    if log is not None:
        total = sum(counts.values())
        log(
            f"enrich 완료 — 이번 run {total}건: enriched={counts['enriched']} "
            f"no_match={counts['no_match']} no_bjdong={counts['no_bjdong']} "
            f"no_jibun={counts['no_jibun']}"
        )
    return counts


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="enrich_building_ledger", description="건축물대장 enrich(비-아파트)"
    )
    p.add_argument("--db", default=str(DEFAULT_DB_PATH))
    p.add_argument("--lock", default=None, help="공유 락(기본 <db디렉토리>/.ingest.lock)")
    p.add_argument("--interval", type=float, default=0.3, help="API 호출 간 최소 간격(초)")
    p.add_argument("--limit", type=int, default=0, help="이번 run 최대 건수(0=무제한)")
    p.add_argument("--batch-size", type=int, default=30)
    p.add_argument("--max-spin", type=float, default=60.0)
    p.add_argument("--inter-batch-sleep", type=float, default=DEFAULT_INTER_BATCH_SLEEP)
    p.add_argument("--sido", default="", help="sgg 앞2(시도) 콤마목록 — 부분 enrich(예: 11,26)")
    args = p.parse_args(argv)
    sido_prefixes = [s.strip() for s in args.sido.split(",") if s.strip()] or None

    conn = get_connection(args.db)
    init_db(conn)
    lock_path = args.lock or str(Path(args.db).resolve().parent / ".ingest.lock")
    throttle = Throttle(args.interval) if args.interval > 0 else None
    lock = ShlockBatch(lock_path, max_spin=args.max_spin)
    run_enrich(
        conn,
        api_key=get_api_key(),
        lock=lock,
        throttle=throttle,
        batch_size=args.batch_size,
        limit=args.limit,
        sido_prefixes=sido_prefixes,
        inter_batch_sleep=args.inter_batch_sleep,
        log=print,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
