"""floorplan_poc 하네스 (P3-2) — parse/join self-test · 키 로딩 get_api_key 경로 (키리스)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import app.settings as settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "spikes"))
import floorplan_poc  # noqa: E402


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
