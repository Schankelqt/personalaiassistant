from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Awaitable

import anthropic
import asyncio

from personal_ai_os.config import Settings, get_settings


@dataclass
class ClaudeResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


class ClaudeClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    def pick_model(self, *, use_haiku: bool) -> str:
        return (
            self._settings.anthropic_model_haiku
            if use_haiku
            else self._settings.anthropic_model_sonnet
        )

    async def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 2048,
    ) -> ClaudeResult:
        resp = await self._with_retries(
            lambda: self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
            )
        )
        text_parts: list[str] = []
        in_tok = getattr(resp.usage, "input_tokens", 0) or 0
        out_tok = getattr(resp.usage, "output_tokens", 0) or 0
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
        text = "".join(text_parts)
        return ClaudeResult(text=text, input_tokens=in_tok, output_tokens=out_tok, model=model)

    async def complete_with_tools(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_executor: Callable[[str, dict[str, Any]], Awaitable[str]],
        max_tokens: int = 2048,
        max_rounds: int = 6,
    ) -> ClaudeResult:
        msgs = list(messages)
        total_in, total_out = 0, 0
        final_text = ""
        for _ in range(max_rounds):
            resp = await self._with_retries(
                lambda: self._client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=system,
                    tools=tools,
                    messages=msgs,
                )
            )
            total_in += getattr(resp.usage, "input_tokens", 0) or 0
            total_out += getattr(resp.usage, "output_tokens", 0) or 0

            has_tool = any(b.type == "tool_use" for b in (resp.content or []))
            if resp.stop_reason == "end_turn" or not has_tool:
                for block in resp.content or []:
                    if block.type == "text":
                        final_text += block.text
                break

            assistant_content: list[dict[str, Any]] = []
            tool_results: list[dict[str, Any]] = []
            for block in resp.content or []:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )
                    result_text = await tool_executor(block.name, block.input or {})
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                        }
                    )
            msgs.append({"role": "assistant", "content": assistant_content})
            msgs.append({"role": "user", "content": tool_results})

        return ClaudeResult(
            text=final_text.strip() or "…",
            input_tokens=total_in,
            output_tokens=total_out,
            model=model,
        )

    async def _with_retries(self, call: Callable[[], Awaitable[Any]]) -> Any:
        delays = (0.6, 1.5, 3.0)
        last_exc: Exception | None = None
        for i, delay in enumerate(delays, start=1):
            try:
                return await call()
            except Exception as exc:
                last_exc = exc
                if i >= len(delays):
                    break
                await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc


def get_claude_client() -> ClaudeClient:
    return ClaudeClient(get_settings())
