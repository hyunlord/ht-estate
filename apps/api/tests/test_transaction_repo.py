"""transaction 적재 — txn_id 결정론 + 멱등 upsert(조인컬럼 보존) + ingest_month/months."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime

import httpx

from app.sources.molit import Trade
from app.store.db import get_connection, init_db
from app.store.transaction_repo import (
    ingest_month,
    ingest_months,
    make_txn_id,
    upsert_transaction,
)
from app.throttle import Throttle

FixtureLoader = Callable[[str], str]
FIXED_NOW = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)


def _trade(**overrides: object) -> Trade:
    base: dict[str, object] = {
        "apt_name": "한양3",
        "legal_dong": "압구정동",
        "road_addr": "압구정로",
        "build_year": 1978,
        "net_area": 161.9,
        "price": 700000,
        "floor": 9,
        "deal_date": date(2025, 4, 1),
        "sgg_cd": "11680",
        "umd_cd": "11000",
        "apt_seq": "11680-380",
        "jibun": "489",
    }
    base.update(overrides)
    return Trade(**base)  # type: ignore[arg-type]


def test_make_txn_id_is_deterministic() -> None:
    assert make_txn_id(_trade()) == make_txn_id(_trade())


def test_make_txn_id_differs_by_price() -> None:
    assert make_txn_id(_trade(price=700000)) != make_txn_id(_trade(price=720000))


def test_make_txn_id_differs_by_floor_and_area() -> None:
    assert make_txn_id(_trade(floor=9)) != make_txn_id(_trade(floor=10))
    assert make_txn_id(_trade(net_area=161.9)) != make_txn_id(_trade(net_area=84.97))


def test_upsert_writes_row_with_nulls_and_provenance() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    txn_id = upsert_transaction(conn, _trade(), updated_at=FIXED_NOW)

    row = conn.execute('SELECT * FROM "transaction" WHERE txn_id = ?', (txn_id,)).fetchone()
    assert row is not None
    assert row["apt_name_raw"] == "한양3"
    assert row["legal_dong"] == "압구정동"
    assert row["price"] == 700000
    assert row["net_area"] == 161.9
    assert row["deal_date"] == "2025-04-01"
    assert row["bjd_code"] == "1168011000"  # sggCd+umdCd → 조인 narrowing 키
    assert row["updated_at"] == FIXED_NOW.isoformat()
    # 조인 컬럼은 적재 시 NULL (T0-4 소관)
    assert row["complex_id"] is None
    assert row["match_confidence"] is None


def test_upsert_idempotent_preserves_join_columns() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    trade = _trade()
    txn_id = upsert_transaction(conn, trade, updated_at=FIXED_NOW)
    conn.commit()

    # T0-4가 조인 결과를 채웠다고 가정 (FK 충족 위해 complex 행 선삽입)
    conn.execute("INSERT INTO complex (complex_id) VALUES ('A10027474')")
    conn.execute(
        'UPDATE "transaction" SET complex_id = ?, match_confidence = ? WHERE txn_id = ?',
        ("A10027474", 0.95, txn_id),
    )
    conn.commit()

    # 재적재(월 재수집) — 행 수 불변 + 조인 컬럼 보존 + updated_at 갱신
    later = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    upsert_transaction(conn, trade, updated_at=later)
    conn.commit()

    rows = conn.execute('SELECT COUNT(*) AS c FROM "transaction"').fetchone()
    assert rows["c"] == 1
    row = conn.execute('SELECT * FROM "transaction" WHERE txn_id = ?', (txn_id,)).fetchone()
    assert row["complex_id"] == "A10027474"  # 보존됨
    assert row["match_confidence"] == 0.95  # 보존됨
    assert row["updated_at"] == later.isoformat()  # 갱신됨


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _paged_handler(trades_body: str, empty_body: str) -> Callable[[httpx.Request], httpx.Response]:
    def handler(req: httpx.Request) -> httpx.Response:
        page = req.url.params.get("pageNo")
        return httpx.Response(200, text=trades_body if page == "1" else empty_body)

    return handler


def test_ingest_month_upserts_and_is_idempotent(load_fixture: FixtureLoader) -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    handler = _paged_handler(load_fixture("molit_trades.xml"), load_fixture("molit_empty.xml"))

    n1 = ingest_month(conn, "11680", "202504", api_key="dummy", client=_client(handler))
    assert n1 == 3
    assert conn.execute('SELECT COUNT(*) AS c FROM "transaction"').fetchone()["c"] == 3

    # 재실행 멱등 — 행 수 불변
    ingest_month(conn, "11680", "202504", api_key="dummy", client=_client(handler))
    assert conn.execute('SELECT COUNT(*) AS c FROM "transaction"').fetchone()["c"] == 3


def test_ingest_dedups_molit_with_without_dong_duplicate(load_fixture: FixtureLoader) -> None:
    # MOLIT는 같은 거래를 동(aptDong) 있는 행/없는 행으로 2번 emit한다(라이브 확인:
    # 강남 202504에서 128건→113행). txn_id가 aptDong을 제외해 한 행으로 dedup되어야 한다.
    conn = get_connection(":memory:")
    init_db(conn)
    dup = load_fixture("molit_dup.xml")
    client = _client(lambda _r: httpx.Response(200, text=dup))
    n = ingest_month(conn, "11680", "202504", api_key="dummy", client=client, updated_at=FIXED_NOW)
    assert n == 2  # 입력 2건
    assert conn.execute('SELECT COUNT(*) AS c FROM "transaction"').fetchone()["c"] == 1  # 1행 dedup


def test_ingest_month_empty_is_graceful(load_fixture: FixtureLoader) -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    empty = load_fixture("molit_empty.xml")
    client = _client(lambda _r: httpx.Response(200, text=empty))
    n = ingest_month(conn, "11680", "202504", api_key="dummy", client=client)
    assert n == 0
    assert conn.execute('SELECT COUNT(*) AS c FROM "transaction"').fetchone()["c"] == 0


class _CountingThrottle(Throttle):
    def __init__(self) -> None:
        super().__init__(0.0)
        self.calls = 0

    def wait(self) -> None:
        self.calls += 1


def test_ingest_months_throttles_between_months(load_fixture: FixtureLoader) -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    handler = _paged_handler(load_fixture("molit_trades.xml"), load_fixture("molit_empty.xml"))
    throttle = _CountingThrottle()

    total = ingest_months(
        conn,
        "11680",
        ["202503", "202504"],
        api_key="dummy",
        throttle=throttle,
        client=_client(handler),
    )
    assert total == 6  # 월당 3건 × 2월 (동일 fixture라 txn_id 동일 → 같은 3행으로 dedup)
    assert throttle.calls == 2  # 월마다 wait()
    assert conn.execute('SELECT COUNT(*) AS c FROM "transaction"').fetchone()["c"] == 3
