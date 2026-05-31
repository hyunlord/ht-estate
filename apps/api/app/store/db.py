"""스토어 경계 — SQLite 연결 + 스키마 초기화.

이 모듈이 저장소 교체 경계다(원칙2: SQLite→Postgres 승급을 막지 말 것).
호출부는 raw 커넥션이 아니라 이 팩토리를 통해서만 DB에 닿는다 →
승급 시 여기만 교체(psycopg/SQLAlchemy 등)하면 된다. 스키마 DDL은
`schema.sql`에 1:1로 두고 여기서 실행만 한다.

이 티켓(T0-1)은 `init_db()`로 테이블을 "생성"하는 것까지. 실제 row 적재는 T0-2+.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# 기본 저장소: 단일 파일 SQLite (설계 §2 기본값). 경로는 호출 시 주입 가능.
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "ht-estate.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def get_connection(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """커넥션 1개 반환. 외래키 강제 ON, row를 이름으로 접근 가능하게 설정.

    `:memory:`를 넘기면 인메모리 DB(테스트용).
    """
    # 파일 DB면 부모 디렉터리를 보장(러너가 data/ 없이 첫 적재할 때 대비). :memory:는 skip.
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI sync 엔드포인트는 스레드풀에서 도므로 커넥션이
    # 생성 스레드와 다른 스레드에서 쓰일 수 있다(요청당 단일 사용이라 안전).
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """`schema.sql`을 실행해 canonical 테이블(complex·transaction·enrichment)을 생성.

    멱등(`CREATE TABLE IF NOT EXISTS`)이라 반복 호출 안전.
    """
    ddl = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(ddl)
    conn.commit()
