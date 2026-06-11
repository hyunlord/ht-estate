"""instant-perf: 인터랙티브 응답 캐시 — 계산 결과만 메모이즈(canon DB write 0 · read-only).

체감-즉시 예산(<~100ms)을 맞추기 위해 무거운 광역 쿼리(DEFAULT_BBOX = 전국/서울 78k 단지의
/markers·/search)를 메모이즈한다. 핫 인터랙션(팬/줌)은 이미 <50ms라 자연스레 미스해도 빠르다.

무효화(stale 0):
- 키에 **DB 데이터 시그니처**(메인 .db + -wal 파일의 mtime·size)를 묶는다. SQLite WAL 모드라
  ingest/enrich/poi가 커밋하면 -wal이 즉시 바뀐다 → 시그니처 변동 → 캐시 미스 → 신선 재계산.
  즉 데이터가 바뀌는 순간 캐시는 무효(신선도/provenance 원칙 보존). 짧은 TTL은 2차 안전망.
- API는 canon을 쓰지 않으므로(read-only) 캐시는 절대 stale write를 만들지 않는다.

거동 보존:
- 시그니처가 None(:memory: — 테스트·미파일 DB)이면 **캐시 우회**(항상 신선 계산) → 거동 100% 동일.
- 미스/예외는 그냥 계산값 반환(graceful·crash 0). 캐시는 빨라지게만 할 뿐 결과를 바꾸지 않는다.
"""

from __future__ import annotations

import sqlite3
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

_MAX_ENTRIES = 256  # 바운드(LRU 축출) — 광역 소수 키라 작아도 충분
_TTL_SECONDS = 120.0  # 2차 안전망(주 무효화는 데이터 시그니처). ingest 주기(일단위)보다 훨씬 짧음
# (태그, 정규화 spec 키) → (저장시각, 데이터 시그니처, 값)
_CACHE: OrderedDict[tuple[str, str], tuple[float, tuple[int, ...], Any]] = OrderedDict()


def _main_db_file(conn: sqlite3.Connection) -> str | None:
    """현 커넥션의 main DB 파일 경로. :memory:면 '' → None(캐시 비활성)."""
    for row in conn.execute("PRAGMA database_list").fetchall():
        if row[1] == "main":  # (seq, name, file)
            return row[2] or None
    return None


def data_signature(conn: sqlite3.Connection) -> tuple[int, ...] | None:
    """DB 데이터 시그니처 — (.db, -wal)의 mtime_ns·size. 미파일(:memory:)이면 None(캐시 우회).

    WAL 모드라 모든 외부 커밋이 -wal을 건드린다 → 어떤 write든 시그니처를 바꾼다(stale 0).
    stat 1회 ~0.01ms(요청당 무시 가능). 파일 부재(체크포인트 직후 등)는 (0,0)로 안전 폴백.
    """
    f = _main_db_file(conn)
    if not f:
        return None

    def _stat(p: Path) -> tuple[int, int]:
        try:
            s = p.stat()
            return (s.st_mtime_ns, s.st_size)
        except OSError:
            return (0, 0)

    main = Path(f)
    return (*_stat(main), *_stat(Path(str(main) + "-wal")))


def cached(tag: str, conn: sqlite3.Connection, key_obj: str, compute: Callable[[], Any]) -> Any:
    """메모이즈 — (tag,key_obj)+데이터 시그니처가 맞고 TTL 내면 캐시값, 아니면 compute() 후 저장.

    시그니처 None(:memory:)이면 캐시 없이 compute() — 테스트/거동 동일성 보장.
    """
    sig = data_signature(conn)
    if sig is None:
        return compute()
    now = time.monotonic()
    key = (tag, key_obj)
    ent = _CACHE.get(key)
    if ent is not None:
        ts, esig, val = ent
        if esig == sig and (now - ts) < _TTL_SECONDS:
            _CACHE.move_to_end(key)  # LRU 갱신
            return val
    val = compute()
    _CACHE[key] = (now, sig, val)
    _CACHE.move_to_end(key)
    while len(_CACHE) > _MAX_ENTRIES:
        _CACHE.popitem(last=False)  # 가장 오래된 것 축출
    return val


def clear() -> None:
    """캐시 비우기 — 테스트·수동 무효화용."""
    _CACHE.clear()
