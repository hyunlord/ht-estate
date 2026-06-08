"""LLM provider 추상화 — OpenAI-호환 chat (base_url + model = config). (E1)

lazy 추출의 LLM 호출을 **OpenAI-호환 provider** 뒤로 추상화한다. Spark 기본 / 작업별 API
override는 **config(base_url·model·key)** 차이일 뿐 코드 불변(원칙: provider 스왑 = 설정).

graceful-degrade(설계 §6): provider 다운·타임아웃·레이트리밋·malformed는 `ProviderError`로
올려 추출기가 **defer**(빈 결과 = miss, 다음 호출 재시도)하게 한다 — **crash 금지**(429 패턴 동형).

키리스: 실 HTTP는 live config(env)에서만. 테스트는 `LLMProvider` 프로토콜을 mock 주입한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import httpx


class ProviderError(RuntimeError):
    """LLM provider 호출 실패(다운·타임아웃·429·비200·malformed) — 추출기 defer 신호."""


class LLMProvider(Protocol):
    """주입형 LLM — (system, user) → 응답 텍스트. 실패는 ProviderError. 키리스 테스트는 mock."""

    def complete(self, system: str, user: str, /) -> str: ...


# OpenAI-호환 chat 엔드포인트 설정(env). 미설정이면 live provider 없음(stub 경로 유지).
BASE_URL_ENV = "ENRICH_LLM_BASE_URL"  # 예: Spark의 OpenAI-호환 엔드포인트 / 다른 API
MODEL_ENV = "ENRICH_LLM_MODEL"
API_KEY_ENV = "ENRICH_LLM_API_KEY"
TIMEOUT_ENV = "ENRICH_LLM_TIMEOUT"  # 초. 로컬 Gemma 경합(~6.5 tok/s) 대비 상향(E1-live).
MAX_TOKENS_ENV = "ENRICH_LLM_MAX_TOKENS"  # 출력 토큰 캡(throughput). 미설정이면 미전송.

# 로컬 Gemma는 GPU 경합 시 2소스 ~22s → 30s 빠듯. env 기본 60s 상향(타임아웃도 graceful defer).
DEFAULT_TIMEOUT = 60.0


@dataclass
class OpenAICompatibleProvider:
    """OpenAI-호환 `/chat/completions` provider. base_url+model로 Spark·임의 API를 동일 코드로.

    client 주입 가능(테스트·풀링). 어떤 실패든 ProviderError로 정규화(graceful-degrade).
    """

    base_url: str
    model: str
    api_key: str = ""
    timeout: float = 30.0
    temperature: float = 0.0
    max_tokens: int | None = None  # 출력 토큰 캡(throughput) — None이면 body에 미포함
    client: httpx.Client | None = None

    def complete(self, system: str, user: str, /) -> str:
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
        }
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens
        own = self.client is None
        cl = self.client or httpx.Client(timeout=self.timeout)
        try:
            resp = cl.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as exc:
            # 다운·타임아웃·레이트리밋(429)·비200·malformed 모두 defer 신호로 정규화
            raise ProviderError(f"LLM provider 실패: {type(exc).__name__}") from exc
        finally:
            if own:
                cl.close()


def provider_from_env() -> LLMProvider | None:
    """env(base_url·model[·key])에서 live provider 구성. 미설정이면 None(stub 경로 유지).

    Spark 기본/API override 모두 같은 코드 — 설정만 다르다. live 활성화는 이 env를 채우는 것.
    """
    base_url = os.environ.get(BASE_URL_ENV, "").strip()
    model = os.environ.get(MODEL_ENV, "").strip()
    if not base_url or not model:
        return None
    try:
        timeout = float(os.environ.get(TIMEOUT_ENV, "").strip() or DEFAULT_TIMEOUT)
    except ValueError:
        timeout = DEFAULT_TIMEOUT
    mt_raw = os.environ.get(MAX_TOKENS_ENV, "").strip()
    max_tokens = int(mt_raw) if mt_raw.isdigit() else None
    return OpenAICompatibleProvider(
        base_url=base_url,
        model=model,
        api_key=os.environ.get(API_KEY_ENV, "").strip(),
        timeout=timeout,
        max_tokens=max_tokens,
    )
