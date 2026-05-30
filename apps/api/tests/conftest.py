"""테스트 공용 fixture 로더.

fixture는 data.go.kr 문서화 스키마 기반으로 수작업 구성한 샘플 응답이다
(라이브 호출 금지·키 불필요). 정확한 태그명은 T0-3 실적재에서 라이브 재검증.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture() -> Callable[[str], str]:
    def _load(name: str) -> str:
        return (FIXTURES_DIR / name).read_text(encoding="utf-8")

    return _load
