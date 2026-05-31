"""scripts 공용 부트스트랩 — apps/api를 sys.path에 추가(스크립트 직접 실행 대비).

`python scripts/X.py`로 직접 실행하면 sys.path[0]이 scripts/라 `app` 패키지를 못 찾는다
(C4~C10 papercut: `PYTHONPATH=.` 필요했음). 각 엔트리포인트가 `import _bootstrap`만 하면
이 모듈이 apps/api를 path에 넣어 `from app...`이 cwd/호출방식 무관하게 동작한다. cron이 깨끗이 돈다.

side-effect import이므로 다른 app 임포트보다 먼저 실행되어야 한다(isort: 서드파티 그룹 선두).
"""

from __future__ import annotations

import sys
from pathlib import Path

_API_ROOT = str(Path(__file__).resolve().parents[1])
if _API_ROOT not in sys.path:
    sys.path.insert(0, _API_ROOT)
