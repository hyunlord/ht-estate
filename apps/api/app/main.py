"""ht-estate API — Phase 0 scaffold.

기능 로직 없음. 도구체인 게이트(ruff·pyright·pytest)와 부팅을 증명하는
헬스 슬라이스만 포함한다. Tier 1 적재/조인/필터는 T0-1+ 소관.
"""

from fastapi import FastAPI

app = FastAPI(title="ht-estate API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    """헬스 체크 — 게이트/스모크용 결정론 엔드포인트."""
    return {"status": "ok"}
