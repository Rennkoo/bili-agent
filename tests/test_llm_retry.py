import asyncio
from dataclasses import replace
from types import SimpleNamespace

from bili_agent.llm import LLMClient
from tests.test_fallback_and_markdown import _settings


class RateLimitError(RuntimeError):
    status_code = 429


class FakeCompletions:
    def __init__(self):
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise RateLimitError("rate limit")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))]
        )


class FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=FakeCompletions())


def test_llm_retries_transient_gateway_error():
    settings = replace(_settings(), llm_max_retries=2, llm_retry_base_seconds=0)
    client = LLMClient(settings)
    client._client = FakeClient()

    result = asyncio.run(client._chat_json("return json"))

    assert result == {"ok": True}
    assert client._client.chat.completions.calls == 2


def test_llm_does_not_retry_bad_request():
    class BadRequestError(RuntimeError):
        status_code = 400

    class BadClient:
        def __init__(self):
            self.calls = 0
            self.chat = SimpleNamespace(completions=self)

        async def create(self, **kwargs):
            self.calls += 1
            raise BadRequestError("invalid prompt")

    client = LLMClient(replace(_settings(), llm_max_retries=2, llm_retry_base_seconds=0))
    fake = BadClient()
    client._client = fake

    try:
        asyncio.run(client._chat_completion(model="test", messages=[]))
    except BadRequestError:
        pass
    else:
        raise AssertionError("expected bad request")
    assert fake.calls == 1
