"""floorplan_poc 하네스 + producer (P3-2-live) — parse/join self-test · 키 로딩 ·
--out 이미지/seed 산출 · seed↔load_floorplan_seed 호환 · 커버리지 (전부 키리스, 라이브 mock)."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import app.settings as settings
from app.enrich.store import read_facts
from app.store.db import get_connection, init_db

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS / "spikes"))
sys.path.insert(0, str(_SCRIPTS))
import floorplan_poc  # noqa: E402
import load_floorplan_seed  # noqa: E402
from auto_enrich import parse_floorplan_output  # noqa: E402

# 1x1 PNG base64(self-test와 동일) — 디코드 시 \x89PNG. 합성 LH 레코드 이미지로 재사용.
_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC"
)
_FEATS = {"bay": 3, "orientation": "남향", "structure": "판상형", "evidence": "전면 3실"}
NOW = datetime(2026, 6, 1, tzinfo=UTC)


def test_selftest_keyless_passes() -> None:
    # parse(base64)+join(P2-4 fuzzy) 로직 self-test — 키리스.
    assert floorplan_poc.main(["--selftest"]) == 0


def test_run_uses_get_api_key_not_raw_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    # G(papercut 교정): --run 키 로딩은 app.settings.get_api_key(= load_dotenv .env 인식) 경로.
    # 생짜 os.environ였다면 이 monkeypatch가 안 먹는다 → 이 테스트가 경로를 증명.
    def _raise() -> str:
        raise settings.MissingApiKeyError("키 없음")

    monkeypatch.setattr(settings, "get_api_key", _raise)
    rc = floorplan_poc.main(["--run", "--limit", "1", "--db", ":memory:"])
    assert rc == 2  # 키 없음 → graceful 2(get_api_key 경로 탔다는 증거)


def test_source_has_no_raw_environ_key_read() -> None:
    # 정적 가드: DATA_GO_KR 키를 os.environ로 직접 읽지 않는다(.env 무시 papercut 재발 방지).
    src = Path(floorplan_poc.__file__ or "").read_text(encoding="utf-8")
    assert 'os.environ.get("DATA_GO_KR' not in src
    assert "get_api_key" in src


# ───────────────────────── producer 유닛 (A: 이미지·seed, B: 커버리지) ─────────────────────────


def test_save_image_writes_png_with_exact_bytes(tmp_path: Path) -> None:
    # A: 디코드 이미지를 lh_<id>.png로 저장 — Web 육안 대조용 경로·바이트 보존.
    data = b"\x89PNG\r\n\x1a\nfloorplan-bytes"
    path = floorplan_poc.save_image(tmp_path / "fp", "111", data)
    assert path == tmp_path / "fp" / "lh_111.png"
    assert path.read_bytes() == data


def test_record_id_prefers_id_key_else_index() -> None:
    assert floorplan_poc.record_id({"id": "abc"}, 7) == "abc"
    assert floorplan_poc.record_id({"hsmpSn": "X9"}, 7) == "X9"
    assert floorplan_poc.record_id({}, 7) == "7"  # id 키 없으면 1-based 순번 폴백


def test_record_source_url_http_passthrough_else_urn_fallback() -> None:
    assert floorplan_poc.record_source_url({"imageUrl": "https://lh/x.png"}, "1") == "https://lh/x.png"
    # url 키 없으면 LH 데이터셋 urn 폴백 — parse_floorplan_output이 urn:을 통과시켜 provenance 유지.
    assert floorplan_poc.record_source_url({}, "42") == "urn:lh:15037046:42"


def test_join_coverage_math_and_lh_prefix_effect() -> None:
    # B: matched/total·율 + LH-접두 정규화 수혜 그룹 매칭.
    samples = [
        {"lh_prefixed": True, "matched": True},
        {"lh_prefixed": True, "matched": False},
        {"lh_prefixed": False, "matched": True},
    ]
    cov = floorplan_poc.join_coverage(samples)
    assert cov["total"] == 3 and cov["matched"] == 2
    assert cov["rate"] == pytest.approx(2 / 3)
    assert cov["lh_prefixed"] == 2 and cov["lh_prefixed_matched"] == 1
    assert "2/3" in floorplan_poc.format_coverage(cov)


def test_join_coverage_empty_no_zero_division() -> None:
    cov = floorplan_poc.join_coverage([])
    assert cov["total"] == 0 and cov["rate"] == 0.0
    floorplan_poc.format_coverage(cov)  # 0건도 포맷 안전


# ───────────────────────── seed ↔ load_floorplan_seed 호환 (C) ─────────────────────────


def _load_db() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    conn.execute("INSERT INTO complex (complex_id, name) VALUES ('K1', '수서1단지')")
    conn.commit()
    return conn


def test_seed_record_roundtrips_into_load_floorplan_seed(tmp_path: Path) -> None:
    # C: producer seed 후보 → parse_floorplan_output 검증 → append → read_records → load_seed →
    #    read_facts. producer가 쓰는 seed가 load_floorplan_seed가 읽는 형식임을 end-to-end로 증명.
    raw = floorplan_poc.to_seed_record("K1", "LH수서1단지", "urn:lh:15037046:1", _FEATS)
    records = parse_floorplan_output(json.dumps(raw, ensure_ascii=False), {"K1"})
    assert len(records) == 1  # 검증 통과(도메인·source·feature)
    seed_path = tmp_path / "floorplan_seed.jsonl"
    from auto_enrich import append_seed  # producer가 쓰는 동일 appender
    append_seed(seed_path, records)
    loaded = load_floorplan_seed.load_seed_records(seed_path)
    conn = _load_db()
    stats = load_floorplan_seed.load_seed(conn, loaded, ttl=timedelta(days=90), now=NOW)
    assert stats["loaded"] == 1
    data = json.loads(read_facts(conn, "K1", "floorplan", now=NOW)[0].value)
    assert data["bay"] == 3 and data["orientation"] == "남향" and data["structure"] == "판상형"


def test_producer_discipline_drops_all_null_and_out_of_domain() -> None:
    # §11 재확인: feature 전부 null / 도메인 밖이면 파서가 drop(producer 경로도 동일 규율).
    all_null = floorplan_poc.to_seed_record("K1", "x", "urn:lh:15037046:1", None)
    assert parse_floorplan_output(json.dumps(all_null, ensure_ascii=False), {"K1"}) == []
    scoring = floorplan_poc.to_seed_record(
        "K1", "x", "urn:lh:15037046:2", {"structure": "좋은구조", "orientation": "남향"}
    )
    out = parse_floorplan_output(json.dumps(scoring, ensure_ascii=False), {"K1"})
    # 점수화 토큰('좋은구조')은 null, 도메인 내 '남향'만 통과.
    assert out and out[0]["structure"] is None and out[0]["orientation"] == "남향"


# ───────────────────────── --out 통합(다운로드·claude -p mock, 키리스) ─────────────────────────


def test_run_out_saves_images_writes_seed_and_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # 라이브 의존(키·다운로드·비전)을 전부 mock → --out producer 경로를 키리스로 구동.
    monkeypatch.setattr(settings, "get_api_key", lambda: "DUMMY")
    records = [
        {"id": "111", "name": "LH수서1단지", "address": "서울특별시 강남구 수서동 750",
         "data": _PNG_B64, "mime": "image/png"},
        {"id": "222", "name": "관악LH3단지", "address": "서울특별시 관악구 신림동 100",
         "data": _PNG_B64, "mime": "image/png"},
    ]
    monkeypatch.setattr(floorplan_poc, "fetch_inventory", lambda key, limit: records[:limit])
    monkeypatch.setattr(floorplan_poc, "extract_features", lambda *a, **k: dict(_FEATS))

    db = tmp_path / "t.db"
    conn = get_connection(str(db))
    init_db(conn)
    conn.execute(
        "INSERT INTO complex (complex_id, name, dong, legal_addr) VALUES "
        "('K1', '수서1단지', '수서동', '서울특별시 강남구 수서동 750')"
    )
    conn.commit()
    conn.close()

    out = tmp_path / "real"
    rc = floorplan_poc.main(["--run", "--limit", "2", "--out", str(out), "--db", str(db)])
    assert rc == 0

    # A. 두 표본 이미지 저장(매칭 무관 — 육안 대조용).
    assert (out / "lh_111.png").exists() and (out / "lh_222.png").exists()

    # A. seed: 매칭된 K1만(수서동→LH 정규화 매칭), urn provenance. 무관 단지(신림동)는 미기록.
    seed_lines = (out / "floorplan_seed.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(seed_lines) == 1
    rec = json.loads(seed_lines[0])
    assert rec["complex_id"] == "K1" and rec["bay"] == 3
    assert rec["source_url"] == "urn:lh:15037046:111"

    # seed ↔ 로더 호환: 산출 seed를 그대로 load_floorplan_seed로 적재.
    loaded = load_floorplan_seed.load_seed_records(out / "floorplan_seed.jsonl")
    conn2 = get_connection(":memory:")
    init_db(conn2)
    conn2.execute("INSERT INTO complex (complex_id, name) VALUES ('K1', '수서1단지')")
    conn2.commit()
    assert load_floorplan_seed.load_seed(
        conn2, loaded, ttl=timedelta(days=90), now=NOW
    )["loaded"] == 1

    # B. 커버리지: matched 1/2 + LH-접두 수혜(LH수서1단지만 접두·매칭).
    captured = capsys.readouterr().out
    assert "1/2" in captured
