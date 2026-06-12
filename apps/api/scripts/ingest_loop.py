"""적재 자동 재개 루프 (C22) — 긴 끊김/프로세스 종료/재부팅 후 자동으로 이어가기.

`_http` 재시도(A)는 짧은 끊김(수십 초~분)을 호출 *내부*에서 라이드아웃한다. 더 긴 끊김이나
프로세스가 죽은 경우엔 이 래퍼가 `ingest_nationwide --resume`를 *완료까지* 반복 호출한다
(C20 원장 덕에 매 호출이 이어서 — 재적재 없음). 일시 오류는 백오프 후 재시도, 영구 오류
(인가·일일캡 = PublicDataError, 영구 4xx)는 즉시 중단(다음 날/cron에서 이어서).

    uv run python scripts/ingest_loop.py --stages transaction --months 202505-202604
    uv run python scripts/ingest_loop.py --stages complex                      # complex 완료까지

cron/systemd로 주기 기동하면 재부팅 후에도 자동 재개된다(런북 참고).
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

import _bootstrap  # noqa: F401  (apps/api를 sys.path에)
import httpx
import ingest_nationwide
from ingest_nationwide import load_codes, pending_regions
from ingest_seoul import recent_months

from app.ingest import DEFAULT_STAGES, STAGE_ORDER, parse_months
from app.sources._http import _is_permanent_status
from app.sources.errors import PublicDataError
from app.store.db import DEFAULT_DB_PATH, get_connection, init_db
from app.store.pipeline_state import bootstrap_pipeline_state_safe


def default_is_permanent(exc: Exception) -> bool:
    """영구 오류 판정 — 인가/일일캡(PublicDataError)·영구 4xx. 일시(전송·5xx·429)는 False."""
    if isinstance(exc, PublicDataError):
        # 코드 있는 PublicDataError(인가'30'·일일캡'22')는 영구 — 루프로 못 푼다(다음 날/cron).
        # 코드 없는 것(result_code=None)은 transient 빈응답(fix/rent-empty-ledger) → 백오프 재시도.
        return exc.result_code is not None
    if isinstance(exc, httpx.HTTPStatusError):
        return _is_permanent_status(exc.response.status_code)
    return False  # TransportError·5xx·429·기타 → 일시로 보고 백오프 재시도


def loop_until_done(
    run_once: Callable[[], None],
    remaining: Callable[[], int],
    *,
    max_runs: int,
    sleep: Callable[[float], None] = time.sleep,
    run_interval: float = 5.0,
    retry_backoff: float = 30.0,
    max_backoff: float = 1800.0,
    is_permanent: Callable[[Exception], bool] = default_is_permanent,
    log: Callable[[str], None] = print,
) -> bool:
    """`run_once()`를 `remaining()==0`까지 반복. 완료 True / max_runs 소진(미완) False.

    일시 오류 → 지수 백오프(capped) 후 같은 run 재시도. 영구 오류 → 즉시 raise(중단).
    각 run은 `--resume`라 이미 한 일을 건너뛰므로 반복이 곧 이어가기다.
    """
    if remaining() == 0:
        log("이미 완료 — 할 일 없음")
        return True
    delay = retry_backoff
    for run in range(1, max_runs + 1):
        try:
            run_once()
        except Exception as exc:  # noqa: BLE001 — 분류 후 재시도/중단
            if is_permanent(exc):
                log(f"[{run}/{max_runs}] 영구 오류 — 중단(다음 실행/cron에서 이어서): {exc!r}")
                raise
            log(f"[{run}/{max_runs}] 일시 오류 — {delay:.0f}s 후 재시도: {exc!r}")
            sleep(delay)
            delay = min(delay * 2, max_backoff)
            continue
        delay = retry_backoff  # 성공 → 백오프 리셋
        left = remaining()
        if left == 0:
            log(f"[{run}/{max_runs}] 적재 완료 ✅")
            return True
        log(f"[{run}/{max_runs}] 진행 — 남은 시군구 {left}, {run_interval:.0f}s 후 계속")
        sleep(run_interval)
    log(f"max_runs({max_runs}) 도달 — 미완(다음 실행/cron에서 이어서)")
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ingest_loop", description="적재 자동 재개 루프(C22)")
    parser.add_argument("--codes-file", default=str(ingest_nationwide.CODES_CSV))
    parser.add_argument("--regions", default="all")
    parser.add_argument("--months", default="")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--stages", default="all")
    parser.add_argument("--interval", type=float, default=0.2, help="API 호출 간 간격(초)")
    parser.add_argument("--max-runs", type=int, default=50, help="최대 재개 횟수(무한루프 가드)")
    parser.add_argument("--run-interval", type=float, default=5.0, help="run 사이 대기(초)")
    parser.add_argument("--retry-backoff", type=float, default=30.0, help="일시오류 첫 대기(초)")
    args = parser.parse_args(argv)

    stages = (
        list(DEFAULT_STAGES) if args.stages == "all"
        else [s.strip() for s in args.stages.split(",") if s.strip()]
    )
    unknown = [s for s in stages if s not in STAGE_ORDER]
    if unknown:
        parser.error(f"알 수 없는 stage: {unknown}")
    months = parse_months(args.months) if args.months else recent_months(date.today())

    codes_path = Path(args.codes_file)

    def _remaining() -> int:
        conn = get_connection(args.db)
        init_db(conn)
        codes = load_codes(codes_path)
        if args.regions != "all":
            wanted = {c.strip() for c in args.regions.split(",") if c.strip()}
            codes = [row for row in codes if row[0] in wanted]
        left = len(pending_regions(conn, codes, stages, months))
        conn.close()
        return left

    def _run_once() -> None:
        nw_argv = [
            "--resume", "--stages", ",".join(stages), "--db", args.db,
            "--codes-file", str(codes_path), "--regions", args.regions,
            "--interval", str(args.interval),
        ]
        if args.months:
            nw_argv += ["--months", args.months]
        ingest_nationwide.main(nw_argv)

    ok = loop_until_done(
        _run_once, _remaining, max_runs=args.max_runs,
        run_interval=args.run_interval, retry_backoff=args.retry_backoff,
    )
    end_conn = get_connection(args.db)  # pipeline-state: run-end 자기서술(provenance 유도·META만)
    bootstrap_pipeline_state_safe(end_conn)
    end_conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
