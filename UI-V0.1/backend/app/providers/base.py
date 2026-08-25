from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Optional, Protocol


class ProviderError(RuntimeError):
    pass


class ProviderUnavailableError(ProviderError):
    pass


class ProviderTimeoutError(ProviderError):
    pass


class ProviderResponseError(ProviderError):
    pass


@dataclass(frozen=True, slots=True)
class ChatResult:
    content: str
    prompt_tokens: Optional[int] = None
    response_tokens: Optional[int] = None


@dataclass(frozen=True, slots=True)
class ChatStreamEvent:
    content: str = ""
    done: bool = False
    prompt_tokens: Optional[int] = None
    response_tokens: Optional[int] = None


class ChatProvider(Protocol):
    async def chat(self, model: str, messages: list[dict]) -> ChatResult:
        ...

    def stream_chat(
        self, model: str, messages: list[dict]
    ) -> AsyncIterator[ChatStreamEvent]:
        ...

    async def list_models(self) -> list[str]:
        ...
