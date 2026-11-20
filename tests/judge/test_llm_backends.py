from types import SimpleNamespace as NS

import pytest

from watchtower.judge.llm import AnthropicBackend

SCHEMA = {"type": "object", "properties": {"page_type": {"type": "string"}}, "required": ["page_type"]}


class FakeMessages:
    def __init__(self, response):
        self.response, self.kwargs = response, None

    async def create(self, **kw):
        self.kwargs = kw
        return self.response


def backend(response, settings):
    b = AnthropicBackend(settings.model_copy(update={"anthropic_api_key": "test", "llm_model": "claude-sonnet-5-5"}))
    b.client = NS(messages=FakeMessages(response))
    return b


def reply(*blocks, stop="end_turn"):
    return NS(content=list(blocks), stop_reason=stop, usage=NS(input_tokens=120, output_tokens=30))


async def test_claude_request_does_not_force_a_tool(settings):
    # Sonnet 5.5 answers a forced tool_choice ("any"/"tool") with HTTP 400.
    b = backend(reply(NS(type="text", text='{"page_type": "tender_list"}')), settings)
    data, tin, tout = await b("sys", "user", SCHEMA)
    kw = b.client.messages.kwargs
    assert "tool_choice" not in kw and "tools" not in kw
    assert '"page_type"' in kw["system"]  # the schema travels in the prompt
    assert data == {"page_type": "tender_list"} and (tin, tout) == (120, 30)


async def test_claude_reply_skips_thinking_blocks(settings):
    b = backend(reply(NS(type="thinking", thinking=""), NS(type="text", text='Here: {"page_type": "login"}')), settings)
    assert (await b("sys", "user", SCHEMA))[0] == {"page_type": "login"}


async def test_claude_refusal_raises(settings):
    b = backend(reply(NS(type="text", text=""), stop="refusal"), settings)
    with pytest.raises(RuntimeError, match="refus"):
        await b("sys", "user", SCHEMA)
