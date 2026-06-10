"""학교 데이터 fetch-if-newer (fetch-school-data) — 반기 3종을 최신본만 받아 저장.

소스: 학구도안내서비스(schoolzone.emac.kr) 공공데이터 목록 — 반기(3월/9월) 갱신. 다운로드는
단순 GET(`/publicData/publicDataFileDownload.do?nttId&atchFileId&fileSn`, 세션 불요)이고 zip
안에 CSV/SHP가 들어있다. 목록 HTML의 제목 날짜로 최신본을 고르고, 매니페스트(school_data_
versions.json)와 비교해 **더 새것만** 받는다(멱등·재실행 안전).

대상 → 로더 기대 경로(school-1·school-2 그대로 소비):
  학교위치 CSV          → data/school_locations.csv
  초등 통학구역 SHP세트  → data/school_zone/ (.shp/.shx/.dbf/.prj …)
  학교-학구도 연계 CSV   → data/school_zone_link.csv

규율: **파일만 read/write — canonical DB write 0** → 지문 163df7…·counts 자명 불변.
graceful-degrade: 도달/파싱/zip 실패 → 로그+해당 데이터 skip+**기존 파일 보존**(부분쓰기·크래시 0).
원자적: zip을 임시로 받아 검증·추출 후 **마지막에만** 목표 경로로 swap. 키리스 테스트는 httpx mock.

    uv run python scripts/fetch_school_data.py            # 3종 fetch-if-newer
    uv run python scripts/fetch_school_data.py --force     # 매니페스트 무시(강제 재취득)
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import _bootstrap  # noqa: F401  (apps/api를 sys.path에)
import httpx

from app.store.db import DEFAULT_DB_PATH

LIST_URL = "https://schoolzone.emac.kr/publicData/publicDataList.do"
DL_URL = "https://schoolzone.emac.kr/publicData/publicDataFileDownload.do"
ATTRIBUTION = "학구도안내서비스(schoolzone.emac.kr)·한국교육시설안전원"

DATA_DIR = Path(DEFAULT_DB_PATH).resolve().parent
MANIFEST = DATA_DIR / "school_data_versions.json"


@dataclass(frozen=True)
class Dataset:
    name: str
    title_substr: str  # 목록 제목 매칭(최신 날짜본 선택)
    target: str  # 저장 경로(파일 or 디렉토리, DATA_DIR 기준)
    is_dir: bool  # True=SHP 세트(디렉토리), False=단일 CSV


DATASETS = (
    Dataset("locations", "학교 위치", "school_locations.csv", is_dir=False),
    Dataset("zone", "초등학교 통학구역", "school_zone", is_dir=True),
    Dataset("link", "학구도 연계정보", "school_zone_link.csv", is_dir=False),
)


@dataclass(frozen=True)
class ListRow:
    ntt_id: str
    atch_file_id: str
    file_sn: str
    title: str
    version_date: str  # 'YYYY.MM.DD'


_ROW_RE = re.compile(
    r'data-nttId="(?P<ntt>\d+)"[^>]*?data-atchFileId="(?P<fid>FILE_\w+)"[^>]*?data-fileSn="(?P<fsn>\d+)"'
)
_DATE_RE = re.compile(r"\((\d{4})\.(\d{2})\.(\d{2})")


def parse_list(html: str) -> list[ListRow]:
    """목록 HTML → 다운로드 가능 행(nttId·atchFileId·fileSn·제목·버전일자). 날짜 없는 행은 제외."""
    rows: list[ListRow] = []
    for m in _ROW_RE.finditer(html):
        seg = re.sub(r"<[^>]+>", " ", html[max(0, m.start() - 900) : m.start()])
        seg = re.sub(r"\s+", " ", seg)
        dm = list(_DATE_RE.finditer(seg))
        if not dm:
            continue  # 날짜 미상 행(표준안 등) skip
        d = dm[-1]
        rows.append(
            ListRow(
                ntt_id=m.group("ntt"), atch_file_id=m.group("fid"), file_sn=m.group("fsn"),
                title=seg[-120:].strip(), version_date=f"{d.group(1)}.{d.group(2)}.{d.group(3)}",
            )
        )
    return rows


def pick_latest(rows: list[ListRow], title_substr: str) -> ListRow | None:
    """title_substr 포함 행 중 **최신 버전일자**. 없으면 None."""
    cands = [r for r in rows if title_substr in r.title]
    return max(cands, key=lambda r: r.version_date) if cands else None


def _place_zip(zip_bytes: bytes, target: Path, is_dir: bool) -> list[str]:
    """zip을 검증·추출 후 **원자적으로** target에 배치. 실패 시 예외(호출부가 graceful)."""
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))  # 손상 zip → BadZipFile
    members = [n for n in zf.namelist() if not n.endswith("/")]
    if not members:
        raise ValueError("zip 비어있음")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if is_dir:
        stage = DATA_DIR / (target.name + ".stage")
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir(parents=True)
        for n in members:
            (stage / Path(n).name).write_bytes(zf.read(n))
        old = DATA_DIR / (target.name + ".old")
        shutil.rmtree(old, ignore_errors=True)
        if target.exists():
            os.replace(target, old)  # 기존 보존(swap)
        os.replace(stage, target)  # 원자적 rename(동일 fs)
        shutil.rmtree(old, ignore_errors=True)
        return [Path(n).name for n in members]
    # 단일 CSV
    csvs = [n for n in members if n.lower().endswith(".csv")] or members
    tmp = DATA_DIR / (target.name + ".tmp")
    tmp.write_bytes(zf.read(csvs[0]))
    os.replace(tmp, target)  # 원자적 replace
    return [Path(csvs[0]).name]


def _load_manifest() -> dict:
    if MANIFEST.exists():
        try:
            return json.loads(MANIFEST.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def fetch_one(
    client: httpx.Client, ds: Dataset, rows: list[ListRow], manifest: dict, *, now: datetime
) -> dict:
    """데이터 1종 fetch-if-newer. 반환=리포트 dict(action·latest·stored·bytes·path). graceful."""
    target = DATA_DIR / ds.target
    stored = (manifest.get(ds.name) or {}).get("version_date")
    rep: dict = {"name": ds.name, "path": str(target), "latest": None,
                 "stored": stored, "action": "", "bytes": 0}
    row = pick_latest(rows, ds.title_substr)
    if row is None:
        rep["action"] = "failed"
        rep["error"] = "목록에서 데이터 못 찾음"
        return rep
    rep["latest"] = row.version_date
    if rep["stored"] is not None and rep["stored"] >= row.version_date and target.exists():
        rep["action"] = "up-to-date"
        return rep
    try:
        url = f"{DL_URL}?nttId={row.ntt_id}&atchFileId={row.atch_file_id}&fileSn={row.file_sn}"
        resp = client.get(url, timeout=120.0, follow_redirects=True)
        resp.raise_for_status()
        blob = resp.content
        placed = _place_zip(blob, target, ds.is_dir)  # 원자적 — 여기 실패해도 기존 보존
        manifest[ds.name] = {
            "version_date": row.version_date, "source_url": url, "attribution": ATTRIBUTION,
            "fetched_at": now.isoformat(), "bytes": len(blob),
            "sha256": hashlib.sha256(blob).hexdigest(), "members": placed,
        }
        rep["action"] = "downloaded"
        rep["bytes"] = len(blob)
    except Exception as exc:  # noqa: BLE001 — graceful: 로그+skip+기존 보존(크래시 0)
        rep["action"] = "failed"
        rep["error"] = f"{type(exc).__name__}: {exc}"
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fetch_school_data")
    ap.add_argument("--force", action="store_true", help="매니페스트 무시·강제 재취득")
    args = ap.parse_args(argv)
    now = datetime.now(UTC)

    manifest = {} if args.force else _load_manifest()
    try:
        with httpx.Client() as client:
            html = client.get(LIST_URL, timeout=60.0, follow_redirects=True).text
            rows = parse_list(html)
            reports = [fetch_one(client, ds, rows, manifest, now=now) for ds in DATASETS]
    except Exception as exc:  # noqa: BLE001 — 목록 도달 실패 = 전체 skip(기존 보존·크래시 0)
        print(f"✗ 목록 도달 실패: {type(exc).__name__}: {exc} — 전부 skip(기존 보존)")
        return 1

    if any(r["action"] == "downloaded" for r in reports):
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"학교 데이터 fetch-if-newer ({now.date()}):")
    for r in reports:
        line = (f"  [{r['action']:^11}] {r['name']:9} latest={r['latest']} "
                f"stored={r['stored']} bytes={r['bytes']} → {r['path']}")
        print(line)
        if r.get("error"):
            print(f"               error: {r['error']}")
    return 0 if all(r["action"] != "failed" for r in reports) else 2


if __name__ == "__main__":
    raise SystemExit(main())
