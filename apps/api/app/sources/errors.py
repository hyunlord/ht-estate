"""공공 API 공통 에러."""

from __future__ import annotations


class PublicDataError(RuntimeError):
    """공공데이터포털 응답이 성공 코드(00/000)가 아닐 때.

    `result_code`/`result_msg`는 응답 헤더에서 추출한 원본을 보존한다
    (예: '30' SERVICE_KEY_IS_NOT_REGISTERED_ERROR, '22' LIMITED_NUMBER_OF...).
    """

    def __init__(self, result_code: str | None, result_msg: str | None) -> None:
        self.result_code = result_code
        self.result_msg = result_msg
        super().__init__(f"public data API error: code={result_code!r} msg={result_msg!r}")
