"""LLM → parameterized SQL converter.

Uses NVIDIA NIM (Llama 3.3 70B) with OpenRouter as automatic fallback.
Async — returns awaitable so the FastAPI event loop is never blocked.
"""

import unicodedata
from os import environ

from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletionSystemMessageParam as SystemMessage,
)
from openai.types.chat import (
    ChatCompletionUserMessageParam as Message,
)

from .markets import llm_hint
from .prompts import system_prompt
from .sql_validator import SQLValidator, SQLQuery


class Filter:
    """NL → validated SQLQuery. Async, JSON-mode, 8s timeout."""

    def __init__(self, model: str = "meta/llama-3.3-70b-instruct") -> None:
        self.model_name = model
        if environ.get("NVIDIA_NIM_API_KEY"):
            self.client = AsyncOpenAI(
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=environ["NVIDIA_NIM_API_KEY"],
            )
        else:
            self.client = AsyncOpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=environ.get("OPENROUTER_API"),
            )

    async def __call__(self, prompt: str, market: str = "global") -> SQLQuery:
        """Generate validated SQL query from natural language prompt.

        `market` scopes the SQL to a set of exchanges — passed via a
        secondary system message so the model treats it as a hard constraint.
        """
        messages = [
            SystemMessage({"role": "system", "content": system_prompt}),
            SystemMessage({"role": "system", "content": llm_hint(market)}),
            Message({"role": "user", "content": prompt}),
        ]
        try:
            completion = await self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                max_completion_tokens=1024,
                temperature=0,
                response_format={"type": "json_object"},
                timeout=8,
            )
        except Exception as exc:
            return SQLQuery(sql_template="", parameters={}, error=f"llm_error: {exc}")

        raw = completion.choices[0].message.content
        if raw is None:
            return SQLQuery(sql_template="", parameters={}, error="empty llm response")

        normalized = unicodedata.normalize("NFKC", raw).strip()
        return SQLValidator().validate_sql_output(normalized)
