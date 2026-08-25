from __future__ import annotations

import unittest

import httpx

from app.providers import ProviderResponseError
from app.providers.ollama import OllamaProvider


class OllamaProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_chat_parses_content_and_token_metadata(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/chat")
            return httpx.Response(
                200,
                content=(
                    b'{"message":{"role":"assistant","content":"hello "},"done":false}\n'
                    b'{"message":{"role":"assistant","content":"world"},"done":false}\n'
                    b'{"message":{"role":"assistant","content":""},"done":true,'
                    b'"prompt_eval_count":17,"eval_count":4}\n'
                ),
            )

        provider = OllamaProvider(
            "http://ollama.test",
            transport=httpx.MockTransport(handler),
        )
        events = [
            event
            async for event in provider.stream_chat(
                "fake-model", [{"role": "user", "content": "hi"}]
            )
        ]

        self.assertEqual("".join(event.content for event in events), "hello world")
        self.assertTrue(events[-1].done)
        self.assertEqual(events[-1].prompt_tokens, 17)
        self.assertEqual(events[-1].response_tokens, 4)

    async def test_stream_chat_rejects_incomplete_stream(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b'{"message":{"role":"assistant","content":"partial"},"done":false}\n',
            )

        provider = OllamaProvider(
            "http://ollama.test",
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(ProviderResponseError):
            async for _ in provider.stream_chat(
                "fake-model", [{"role": "user", "content": "hi"}]
            ):
                pass
