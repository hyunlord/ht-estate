"""K-apt 풀필드 재적재(P0) — 이미 적재된 22k 단지의 0% 컬럼을 채워 죽은 criterion을 깨운다.

배경: P4-1에서 V4 풀필드(elevator/cctv/subway/heat …) 파싱·스키마·REGISTRY 등록은 됐지만,
기존 단지 행은 풀필드 *이전*에 적재돼 컬럼이 NULL이다(코드 검증 ≠ 데이터 백필). 전국 cron은
`--resume`로 기적재 시군구의 complex 단계를 skip하므로 영원히 안 채워진다. 이 스크립트는 기존
complex_id를 직접 순회해 V4 basis+detail을 재fetch → `upsert_complex`로 멱등 갱신한다.

규율:
- **geocode 보존 1순위** — `upsert_complex`의 컬럼셋에 lat/lng·geo_*가 없어 UPDATE가 절대 안
  건드린다(기존 행은 전부 UPDATE 경로). 신규 소스·스키마 변경 없음.
- **resume-safe** — 완료 complex_id를 `ingest_progress`(stage='complex_refill')에 기록 → 재개 skip.
- **멱등** — 같은 단지 2회=1회(레저 skip). None/실패는 미기록 → 다음 패스 재시도.
- **cron-safe** — 공유 `.ingest.lock`을 **배치단위**로 잡았다 푼다(cron tick과 직렬화하되 굶기지
  않음). 거래/목록 cron(:15/:45·:00/:30)이 재적재 중에도 배치 사이에 끼어들 수 있다.
- rate-bound — `--interval` throttle + `_http` 백오프. `--limit`으로 run 바운드(일일캡), 멀티데이.

    uv run python scripts/refill_kapt_fields.py --limit 200          # 이번 run 200단지(캡 보존)
    uv run python scripts/refill_kapt_fields.py                      # 무제한(resume로 누적)
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

import _bootstrap  # noqa: F401  (side-effect: apps/api를 sys.path에)

from app.settings import get_api_key
from app.sources.errors import PublicDataError
from app.sources.kapt import ComplexInfo, fetch_complex_info
from app.store.complex_repo import upsert_complex
from app.store.db import DEFAULT_DB_PATH, get_connection, init_db
from app.store.progress_repo import record_month
from app.throttle import Throttle

# 재적재 진행 레저 — ingest_progress 재사용(스키마 변경 0). region 칸에 complex_id, month는 센티넬.
REFILL_STAGE = "complex_refill"
_REFILL_MONTH = "-"
SHLOCK_BIN = "/usr/bin/shlock"


class _CompletedLike(Protocol):
    """shlock 러너 결과 — `.returncode`만 본다(subprocess.CompletedProcess·테스트 stub 양립)."""

    returncode: int


def all_complex_ids(
    conn: sqlite3.Connection, sido_prefixes: list[str] | None = None
) -> list[str]:
    """대상 complex_id — 결정론 순서(resume+limit가 같은 prefix를 안정적으로 처리).

    `sido_prefixes`(bjd_code 앞2 = 시도코드, 예 ['11','26'])가 주어지면 그 시도만, 시도코드
    오름차순(서울11→광역시26+→…)으로 정렬해 **도심 우선** 백필. 없으면 전 단지.
    """
    if sido_prefixes:
        placeholders = ",".join("?" * len(sido_prefixes))
        rows = conn.execute(
            f"SELECT complex_id FROM complex WHERE substr(bjd_code, 1, 2) IN ({placeholders}) "
            "ORDER BY substr(bjd_code, 1, 2), complex_id",
            list(sido_prefixes),
        ).fetchall()
    else:
        rows = conn.execute("SELECT complex_id FROM complex ORDER BY complex_id").fetchall()
    return [r[0] for r in rows]


def refilled_ids(conn: sqlite3.Connection) -> set[str]:
    """재적재 완료 complex_id 집합 — 재개 skip 판정용(한 번에 로드)."""
    rows = conn.execute(
        "SELECT region FROM ingest_progress WHERE stage = ?", (REFILL_STAGE,)
    ).fetchall()
    return {r[0] for r in rows}


def mark_refilled(conn: sqlite3.Connection, complex_id: str, rows: int) -> None:
    """complex_id 재적재 완료를 레저에 기록(멱등). 성공 upsert 후에만 호출."""
    record_month(conn, REFILL_STAGE, complex_id, _REFILL_MONTH, rows)


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class ShlockBatch:
    """배치 단위 공유 락 — cron과 직렬화(동시 data.go.kr 0 불변)하되 배치 사이 release로 굶김 방지.

    cron_ingest.sh와 **같은** shlock 메커니즘/락 파일을 쓴다(살아있는 PID가 잡으면 skip, 죽은
    PID면 자동 탈취). 배치 시작에 spin-acquire(짧게 양보 후 재시도), 끝에 release. cron tick은
    수 초로 짧으니 spin은 곧 풀린다. max_spin 초과(=cron이 장시간 점유)면 포기 → 이번 run은
    양보하고 다음 run이 resume.
    """

    def __init__(
        self,
        lock_path: str,
        *,
        runner: Callable[..., _CompletedLike] = subprocess.run,
        pid: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        spin_interval: float = 2.0,
        max_spin: float = 60.0,
    ) -> None:
        self.lock_path = lock_path
        self._runner = runner
        self._pid = pid if pid is not None else os.getpid()
        self._sleep = sleep
        self._clock = clock
        self._spin_interval = spin_interval
        self._max_spin = max_spin

    def _try_acquire(self) -> bool:
        result = self._runner(
            [SHLOCK_BIN, "-f", self.lock_path, "-p", str(self._pid)],
            capture_output=True,
        )
        return result.returncode == 0

    def _spin_acquire(self) -> bool:
        start = self._clock()
        while True:
            if self._try_acquire():
                return True
            if self._clock() - start >= self._max_spin:
                return False  # cron 장시간 점유 — 이번 run 양보(resume)
            self._sleep(self._spin_interval)

    def _release(self) -> None:
        try:
            os.remove(self.lock_path)
        except FileNotFoundError:
            pass

    @contextmanager
    def __call__(self) -> Iterator[bool]:
        acquired = self._spin_acquire()
        try:
            yield acquired
        finally:
            if acquired:
                self._release()


def run_refill(
    conn: sqlite3.Connection,
    *,
    fetch_info: Callable[[str], ComplexInfo | None],
    lock: Callable[[], object],
    throttle: Throttle | None,
    batch_size: int,
    limit: int,
    sido_prefixes: list[str] | None = None,
    inter_batch_sleep: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] | None = None,
) -> int:
    """미완료 단지를 배치 단위로 재적재. 처리(성공 upsert)한 단지 수 반환.

    배치마다 공유락을 잡고(점유 중이면 굶지 않고 양보=중단), 단지별 fetch→upsert→레저기록.
    fetch_info가 None을 주면 그 단지는 미기록(다음 패스 재시도). lat/lng는 upsert가 안 건드림.
    `sido_prefixes`로 도심 우선 부분 백필. 일일캡 등 `PublicDataError`면 이번 run을 우아하게
    중단(resume) — 캡 초과로 거래 cron/키를 막지 않는다.

    `inter_batch_sleep`>0이면 **배치 락을 놓은 뒤 다음 배치 acquire 전**에 그만큼 양보한다.
    배치 release 직후 즉시 재acquire(microsecond 창)면 *단발성* cron tick(cron_ingest.sh는
    tick당 shlock 1회 시도·재시도 없음)이 그 창을 거의 못 잡아 [skip]된다 → 실측 starve.
    양보 창은 락 파일이 비는 *실제* 구간이라 그 사이 cron이 shlock으로 락을 만들어 획득한다
    (0=무양보, known-idle 전용). sleep은 **락을 놓은 뒤에만** 호출 — 보유 중 sleep 금지.
    """
    done = refilled_ids(conn)
    pending = [cid for cid in all_complex_ids(conn, sido_prefixes) if cid not in done]
    if limit > 0:
        pending = pending[:limit]
    if log is not None:
        log(f"재적재 대상 {len(pending)}단지 (완료 {len(done)} skip, batch={batch_size})")

    batches = list(_chunks(pending, batch_size))
    processed = 0
    for batch_index, batch in enumerate(batches):
        with lock() as acquired:  # type: ignore[attr-defined]
            if not acquired:
                if log is not None:
                    log("공유락 점유 중(cron 실행) — 이번 run 양보, 다음 run 재개")
                break
            for complex_id in batch:
                if throttle is not None:
                    throttle.wait()
                try:
                    info = fetch_info(complex_id)
                except PublicDataError as exc:
                    if log is not None:
                        log(
                            f"공공API 오류 code={exc.result_code} ({exc.result_msg}) — "
                            "일일캡/일시 추정, 이번 run 중단(레저로 다음 run 재개)"
                        )
                    return processed  # 캡 초과 등 — 우아하게 중단(키/거래cron 미차단)
                if info is None:
                    continue  # 실패/없음 — 미기록(다음 패스 재시도)
                upsert_complex(conn, info)  # K-apt 컬럼만 갱신 — lat/lng·geo_* 불변
                mark_refilled(conn, complex_id, 1)
                processed += 1
        # ── 락 release 완료 지점 ── 마지막 배치가 아니면 cron에 양보 창을 연다.
        if inter_batch_sleep > 0 and batch_index < len(batches) - 1:
            sleep(inter_batch_sleep)
        if log is not None and processed and processed % 200 == 0:
            log(f"  …재적재 {processed}단지 진행")
    if log is not None:
        log(f"재적재 완료 — 이번 run {processed}단지")
    return processed


# 활성-cron 안전 기본 양보(초). cron_ingest.sh는 tick당 shlock 1회만 시도(재시도 없음)이라
# "재시도 간격"이라는 상수가 없다 → 기준은 락 release/재acquire 지연(sub-second)과 배치 보유
# 시간(batch_size×interval≈30×0.6=18s). 2초면 microsecond 창을 *수 초의 실제* 빈-락 창으로 키워
# 단발 cron이 그 사이 shlock으로 락을 만들 수 있고, 배치당 비용은 ~10%(시골/P5-1b 헤비 적재 안전).
# 0=무양보(이미 끝난 도심 백필처럼 known-idle 전용).
DEFAULT_INTER_BATCH_SLEEP = 2.0


def main(
    argv: list[str] | None = None,
    *,
    runner: Callable[..., _CompletedLike] = subprocess.run,
) -> int:
    parser = argparse.ArgumentParser(
        prog="refill_kapt_fields", description="K-apt 풀필드 재적재(P0 · resume·멱등·cron-safe)"
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite 경로")
    parser.add_argument(
        "--lock",
        default=None,
        help="공유 락 파일(기본 <db디렉토리>/.ingest.lock — cron과 동일 직렬화)",
    )
    parser.add_argument("--interval", type=float, default=0.6, help="API 호출 간 최소 간격(초)")
    parser.add_argument("--limit", type=int, default=0, help="이번 run 최대 단지 수(0=무제한)")
    parser.add_argument("--batch-size", type=int, default=30, help="락 1회당 처리 단지 수")
    parser.add_argument(
        "--max-spin", type=float, default=60.0, help="배치 락 spin 최대 대기(초·초과시 양보)"
    )
    parser.add_argument(
        "--inter-batch-sleep",
        type=float,
        default=DEFAULT_INTER_BATCH_SLEEP,
        help="배치 release 후 다음 acquire 전 cron 양보(초). 0=무양보(known-idle 전용)",
    )
    parser.add_argument(
        "--sido",
        default="",
        help="bjd_code 앞2(시도코드) 콤마목록 — 도심 우선 부분 백필(예: 11,26,27). 빈값=전 단지",
    )
    args = parser.parse_args(argv)
    sido_prefixes = [s.strip() for s in args.sido.split(",") if s.strip()] or None

    conn = get_connection(args.db)
    init_db(conn)
    lock_path = args.lock or str(Path(args.db).resolve().parent / ".ingest.lock")
    api_key = get_api_key()
    throttle = Throttle(args.interval) if args.interval > 0 else None
    lock = ShlockBatch(lock_path, runner=runner, max_spin=args.max_spin)

    run_refill(
        conn,
        fetch_info=lambda cid: fetch_complex_info(cid, api_key=api_key),
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
