"""학교 데이터 fetcher (fetch-school-data) — fetch-if-newer·graceful·원자적·매니페스트. 키리스.

실 네트워크 0(httpx MockTransport). 파일만 read/write(tmp DATA_DIR) — canonical DB 무접촉.
"""

from __future__ import annotations

import io
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch_school_data as fsd  # noqa: E402

NOW = datetime(2026, 6, 10, tzinfo=UTC)

# 목록 HTML fixture — 위치(신 2026.03 / 구 2025.09) + 통학구역 + 연계. 행 구조는 실페이지 모사.
def _row(title: str, ntt: str, fid: str) -> str:
    # 실페이지 구조 모사: 제목(날짜) 다음에 data-* 속성을 가진 다운로드 앵커. [^>]는 줄바꿈도 매칭.
    return (
        f'<tr><td>{title}</td>\n'
        f' <a class="down" data-nttId="{ntt}"\n'
        f'    data-atchFileId="{fid}" data-fileSn="0">파일다운로드</a></tr>\n'
    )


_HTML = (
    _row("초중고 학교 위치(2026.03.20.)", "2958", "FILE_000000100002980")
    + _row("초중고 학교 위치(2025.09.22.)", "2900", "FILE_OLD0001")  # 구버전
    + _row("초등학교 통학구역 및 공동통학구역(2026.03.20.)", "2957", "FILE_000000100002979")
    + _row("학교-학구도 연계정보(2026.03.20.)", "2952", "FILE_000000100002974")
)


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(fsd, "DATA_DIR", tmp_path)
    monkeypatch.setattr(fsd, "MANIFEST", tmp_path / "school_data_versions.json")
    return tmp_path


def _client(handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(transport=httpx.MockTransport(handler))


# ── parse / pick ──
def test_parse_list_extracts_rows_and_dates() -> None:
    rows = fsd.parse_list(_HTML)
    assert len(rows) == 4
    by_ntt = {r.ntt_id: r for r in rows}
    assert by_ntt["2958"].version_date == "2026.03.20"
    assert by_ntt["2958"].atch_file_id == "FILE_000000100002980"


def test_pick_latest_picks_newest_matching() -> None:
    rows = fsd.parse_list(_HTML)
    r = fsd.pick_latest(rows, "학교 위치")
    assert r is not None and r.ntt_id == "2958" and r.version_date == "2026.03.20"  # 구버전 아님


# ── fetch_one: newer → download + 원자적 배치 + 매니페스트 ──
def test_fetch_newer_downloads_and_places_csv(_tmp_data: Path) -> None:
    rows = fsd.parse_list(_HTML)
    zb = _zip({"한국교육시설안전원_초중등학교위치_20260320.csv": "학교ID,위도\nS1,37.5\n".encode()})
    ds = fsd.DATASETS[0]  # locations
    rep = fsd.fetch_one(_client(lambda r: httpx.Response(200, content=zb)), ds, rows, {}, now=NOW)
    assert rep["action"] == "downloaded" and rep["latest"] == "2026.03.20"
    out = _tmp_data / "school_locations.csv"
    assert out.exists() and out.read_text(encoding="utf-8").startswith("학교ID")


def test_fetch_shp_set_placed_as_dir(_tmp_data: Path) -> None:
    rows = fsd.parse_list(_HTML)
    zb = _zip({"초등학교통학구역.shp": b"SHP", "초등학교통학구역.dbf": b"DBF",
               "초등학교통학구역.prj": b"PRJ", "초등학교통학구역.shx": b"SHX"})
    ds = fsd.DATASETS[1]  # zone (dir)
    rep = fsd.fetch_one(_client(lambda r: httpx.Response(200, content=zb)), ds, rows, {}, now=NOW)
    assert rep["action"] == "downloaded"
    d = _tmp_data / "school_zone"
    assert d.is_dir()
    assert (d / "초등학교통학구역.shp").exists() and (d / "초등학교통학구역.prj").exists()


# ── up-to-date → skip(다운로드 0) ──
def test_uptodate_skips(_tmp_data: Path) -> None:
    (_tmp_data / "school_locations.csv").write_text("기존", encoding="utf-8")
    manifest = {"locations": {"version_date": "2026.03.20"}}
    calls = []
    def handler(r: httpx.Request) -> httpx.Response:
        calls.append(r.url)
        return httpx.Response(200, content=b"")
    rep = fsd.fetch_one(_client(handler), fsd.DATASETS[0], fsd.parse_list(_HTML), manifest, now=NOW)
    assert rep["action"] == "up-to-date" and calls == []  # 다운로드 시도 0
    assert (_tmp_data / "school_locations.csv").read_text(encoding="utf-8") == "기존"  # 보존


# ── graceful: 다운로드 실패 → skip + 기존 보존 + 크래시 0 ──
def test_download_error_graceful_preserves_existing(_tmp_data: Path) -> None:
    out = _tmp_data / "school_locations.csv"
    out.write_text("기존데이터", encoding="utf-8")

    def boom(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    manifest = {"locations": {"version_date": "2025.09.22"}}  # 구버전 → newer 시도하다 실패
    rep = fsd.fetch_one(_client(boom), fsd.DATASETS[0], fsd.parse_list(_HTML), manifest, now=NOW)
    assert rep["action"] == "failed" and "error" in rep  # 크래시 아님
    assert out.read_text(encoding="utf-8") == "기존데이터"  # 기존 파일 보존
    assert "version_date" in manifest["locations"]  # 매니페스트 미갱신(구버전 그대로)
    assert manifest["locations"]["version_date"] == "2025.09.22"


# ── 손상 zip → 원자적 보존(부분쓰기 0) ──
def test_corrupt_zip_preserves_existing(_tmp_data: Path) -> None:
    out = _tmp_data / "school_locations.csv"
    out.write_text("원본", encoding="utf-8")
    rep = fsd.fetch_one(
        _client(lambda r: httpx.Response(200, content=b"NOT-A-ZIP")),
        fsd.DATASETS[0], fsd.parse_list(_HTML), {}, now=NOW,
    )
    assert rep["action"] == "failed"
    assert out.read_text(encoding="utf-8") == "원본"  # 손상이어도 기존 보존(원자적)
