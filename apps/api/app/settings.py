"""환경설정 — 공공데이터포털 API 키 로드.

키는 env `DATA_GO_KR_API_KEY`에서 읽는다. 미설정 시 명확한 에러를 던져
"키가 없어서 실패"를 모호한 네트워크 에러와 구분한다.

주의(decoded key): data.go.kr은 encoded/decoded 두 형태의 서비스키를 준다.
httpx가 `params=`를 다시 URL 인코딩하므로 **decoded 키**를 넣어야 한다.
encoded 키를 넣으면 이중 인코딩으로 SERVICE_KEY_IS_NOT_REGISTERED_ERROR가 난다.
"""

from __future__ import annotations

import os

from dotenv import find_dotenv, load_dotenv

API_KEY_ENV = "DATA_GO_KR_API_KEY"

# 루트 .env를 자동 로딩(export 불필요). override=False라 실제 환경변수가 항상 이긴다.
# 클린 클론·CI엔 .env가 없으므로(gitignore) 아무것도 로드되지 않음 →
# 게이트는 키 없이 결정론적으로 돈다(테스트는 키를 요구하지 않는다).
load_dotenv(find_dotenv(usecwd=True), override=False)


class MissingApiKeyError(RuntimeError):
    """`DATA_GO_KR_API_KEY`가 비어있거나 미설정."""


def get_api_key() -> str:
    """공공데이터포털 서비스키(decoded) 반환. 없으면 `MissingApiKeyError`.

    클라이언트는 `api_key`를 명시 주입할 수 있어 테스트는 이 함수를 안 탄다
    (라이브 키 불필요).
    """
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        raise MissingApiKeyError(
            f"환경변수 {API_KEY_ENV}가 설정되지 않았습니다. "
            f".env.example을 참고해 decoded 서비스키를 설정하세요."
        )
    return key
