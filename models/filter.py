import json
import unicodedata
from os import environ

from openai import OpenAI
from openai.types.chat import (
    ChatCompletionSystemMessageParam as SystemMessage,
)
from openai.types.chat import (
    ChatCompletionUserMessageParam as Message,
)

from .prompts import system_prompt
from .sql_validator import SQLValidator, SQLError, SQLQuery


class Filter:
    def __init__(self, model: str = "qwen/qwen3.5-397b-a17b") -> None:
        self.model_name = model
        self.api_key = environ.get("NVIDIA_NIM_API_KEY") or environ.get("OPENROUTER_API") or None
        # Prefer NVIDIA NIM; fall back to OpenRouter
        if environ.get("NVIDIA_NIM_API_KEY"):
            self.client = OpenAI(
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=environ["NVIDIA_NIM_API_KEY"],
            )
        else:
            self.client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=environ.get("OPENROUTER_API"),
            )

    def __call__(self, prompt: str) -> SQLQuery:
        """Generate SQL query from natural language prompt."""
        messages = [
            SystemMessage({"role": "system", "content": system_prompt}),
            Message({"role": "user", "content": prompt}),
        ]
        completion_obj = self.client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            max_completion_tokens=4096,
            temperature=0,
        )
        raw_completion = completion_obj.choices[0].message.content
        if raw_completion is None:
            return SQLQuery(sql_template="", parameters={}, error="empty llm response")

        normalized_completion = unicodedata.normalize("NFKC", raw_completion).strip()

        # Validate the LLM output
        validator = SQLValidator()
        return validator.validate_sql_output(normalized_completion)


def validate_sql_output(json_output: str | None) -> SQLQuery:
    """Validate SQL output from LLM (convenience function)."""
    if json_output is None:
        return SQLQuery(sql_template="", parameters={}, error="null output")

    validator = SQLValidator()
    return validator.validate_sql_output(json_output)
