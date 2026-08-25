from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from .base import (
    ChatResult,
    ChatStreamEvent,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


class OllamaProvider:
    def __init__(
        self,
        base_url: str,
        request_timeout_seconds: float = 300.0,
        models_timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.request_timeout_seconds = request_timeout_seconds
        self.models_timeout_seconds = models_timeout_seconds
        self.transport = transport

    async def chat(self, model: str, messages: list[dict]) -> ChatResult:
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0},
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.request_timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.ConnectError as error:
            raise ProviderUnavailableError(
                f"Cannot connect to Ollama at {self.base_url}. Start Ollama first."
            ) from error
        except httpx.TimeoutException as error:
            raise ProviderTimeoutError("Ollama request timed out") from error
        except httpx.HTTPStatusError as error:
            raise ProviderResponseError(
                f"Ollama returned {error.response.status_code}: {error.response.text}"
            ) from error
        except (httpx.RequestError, ValueError) as error:
            raise ProviderResponseError(f"Invalid Ollama response: {error}") from error

        content = data.get("message", {}).get("content", "").strip()
        if not content:
            raise ProviderResponseError("Ollama returned an empty assistant message")
        return ChatResult(
            content=content,
            prompt_tokens=data.get("prompt_eval_count"),
            response_tokens=data.get("eval_count"),
        )

    async def stream_chat(
        self, model: str, messages: list[dict]
    ) -> AsyncIterator[ChatStreamEvent]:
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": 0},
        }
        received_content = False
        received_done = False
        try:
            timeout = httpx.Timeout(self.request_timeout_seconds)
            async with httpx.AsyncClient(
                timeout=timeout,
                transport=self.transport,
            ) as client:
                async with client.stream(
                    "POST", f"{self.base_url}/api/chat", json=payload
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                        except ValueError as error:
                            raise ProviderResponseError(
                                f"Invalid Ollama stream event: {line[:200]}"
                            ) from error
                        if data.get("error"):
                            raise ProviderResponseError(str(data["error"]))
                        content = data.get("message", {}).get("content", "")
                        if content:
                            received_content = True
                            yield ChatStreamEvent(content=content)
                        if data.get("done"):
                            received_done = True
                            yield ChatStreamEvent(
                                done=True,
                                prompt_tokens=data.get("prompt_eval_count"),
                                response_tokens=data.get("eval_count"),
                            )
        except httpx.ConnectError as error:
            raise ProviderUnavailableError(
                f"Cannot connect to Ollama at {self.base_url}. Start Ollama first."
            ) from error
        except httpx.TimeoutException as error:
            raise ProviderTimeoutError("Ollama request timed out") from error
        except httpx.HTTPStatusError as error:
            raise ProviderResponseError(
                f"Ollama returned {error.response.status_code}"
            ) from error
        except httpx.RequestError as error:
            raise ProviderResponseError(f"Invalid Ollama response: {error}") from error

        if not received_content:
            raise ProviderResponseError("Ollama returned an empty assistant message")
        if not received_done:
            raise ProviderResponseError("Ollama stream ended before completion")

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(
                timeout=self.models_timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                data = response.json()
        except httpx.ConnectError as error:
            raise ProviderUnavailableError(
                f"Cannot connect to Ollama at {self.base_url}"
            ) from error
        except httpx.TimeoutException as error:
            raise ProviderTimeoutError("Ollama model listing timed out") from error
        except httpx.HTTPStatusError as error:
            raise ProviderResponseError(
                f"Ollama returned {error.response.status_code}: {error.response.text}"
            ) from error
        except (httpx.RequestError, ValueError) as error:
            raise ProviderResponseError(f"Invalid Ollama response: {error}") from error

        return [
            item["name"]
            for item in data.get("models", [])
            if isinstance(item, dict) and item.get("name")
        ]
