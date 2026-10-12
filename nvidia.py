from __future__ import annotations

import json
from collections.abc import Iterator
from functools import lru_cache

from openai import OpenAI, OpenAIError

from config import settings


class Nvidia:
  def __init__(self) -> None:
    if not settings.nvidia_api_key:
      raise RuntimeError("NVIDIA_API_KEY is not configured")

    self.client = OpenAI(
      base_url="https://integrate.api.nvidia.com/v1",
      api_key=settings.nvidia_api_key,
      timeout=120.0,
      max_retries=0,
    )

  def structured(self, payload: dict, prompt: str, schema):
    """Generate a structured response from an existing JSON payload."""
    request = (
      f"{prompt}\n\nReturn valid JSON matching this schema:\n"
      f"{json.dumps(schema.model_json_schema(), ensure_ascii=False)}"
    )
    try:
      response = self.client.chat.completions.create(
        model=settings.nvidia_model,
        messages=[
          {"role": "system", "content": request},
          {
            "role": "user",
            "content": json.dumps(
              payload,
              ensure_ascii=False,
              default=str,
            ),
          },
        ],
        temperature=0,
        max_tokens=4096,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        response_format={"type": "json_object"},
      )
    except OpenAIError as exc:
      raise RuntimeError("NVIDIA request failed") from exc

    content = response.choices[0].message.content if response.choices else None
    if not content:
      raise RuntimeError("NVIDIA returned empty output")

    return schema.model_validate_json(content)

  def stream(self, prompt: str) -> Iterator[str]:
    """Stream generated text without printing from the service module."""
    try:
      completion = self.client.chat.completions.create(
        model=settings.nvidia_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=1,
        top_p=0.95,
        max_tokens=16384,
        extra_body={
          "chat_template_kwargs": {"enable_thinking": True},
          "reasoning_budget": 16384,
        },
        stream=True,
      )
    except OpenAIError as exc:
      raise RuntimeError("NVIDIA request failed") from exc

    for chunk in completion:
      if not chunk.choices:
        continue
      content = chunk.choices[0].delta.content
      if content:
        yield content


@lru_cache
def nvidia() -> Nvidia:
  return Nvidia()