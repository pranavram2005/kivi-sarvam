"""Concrete LLM providers.

Each provider exposes the same operation: given a system prompt, a user prompt
and a JSON schema, return a parsed object plus its usage. Every provider uses
its platform's native structured-output mode so the pipeline never has to
babysit malformed JSON.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from backend.config import get_settings
from backend.llm.base import LLMProvider, LLMResult, LLMUsage, parse_json_object, price
from backend.memory.text import estimate_tokens


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------
class AnthropicProvider(LLMProvider):
    """Claude via the official `anthropic` SDK.

    Notes on the request shape:
      * `output_config.format` pins the response to our JSON schema.
      * `thinking: {"type": "adaptive"}` lets the model decide how much to
        reason; `output_config.effort` is the cost dial. Memory extraction runs
        once per transcript over a 500-record corpus, so it is called at low
        effort; answering - where correctness matters most - runs higher.
      * `fallbacks` routes around a safety refusal instead of failing the run.
    """

    name = "anthropic"

    def __init__(self, model: str | None = None) -> None:
        import anthropic  # optional dependency, imported lazily

        self._anthropic = anthropic
        self.model = model or "claude-opus-5"
        self._client = anthropic.Anthropic()

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int = 4096,
        effort: str = "medium",
    ) -> LLMResult:
        started = time.perf_counter()
        try:
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                thinking={"type": "adaptive"},
                output_config={
                    "effort": effort,
                    "format": {"type": "json_schema", "schema": schema},
                },
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except Exception as exc:
            return LLMResult(
                error=f"{type(exc).__name__}: {exc}",
                usage=LLMUsage(
                    provider=self.name,
                    model=self.model,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    calls=1,
                ),
            )

        latency_ms = (time.perf_counter() - started) * 1000

        if getattr(response, "stop_reason", None) == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            return LLMResult(
                error=f"model refused the request (category={category})",
                usage=LLMUsage(
                    provider=self.name,
                    model=self.model,
                    latency_ms=latency_ms,
                    calls=1,
                ),
            )

        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        usage_obj = getattr(response, "usage", None)
        input_tokens = int(getattr(usage_obj, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage_obj, "output_tokens", 0) or 0)
        model_used = getattr(response, "model", self.model) or self.model

        return LLMResult(
            data=parse_json_object(text),
            text=text,
            usage=LLMUsage(
                provider=self.name,
                model=model_used,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                cost_usd=price(model_used, input_tokens, output_tokens),
                calls=1,
            ),
        )


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------
class OpenAIProvider(LLMProvider):
    """Anything speaking the OpenAI chat-completions dialect.

    Groq subclasses this by pointing `base_url` elsewhere, which is the whole
    difference between the two providers.
    """

    name = "openai"
    default_model = "gpt-4o-mini"
    base_url: str | None = None
    api_key_env = "OPENAI_API_KEY"

    def __init__(self, model: str | None = None) -> None:
        from openai import OpenAI  # optional dependency

        self.model = model or self.default_model
        key = os.getenv(self.api_key_env)
        if not key:
            raise RuntimeError(f"{self.api_key_env} is not set")
        self._client = OpenAI(api_key=key, base_url=self.base_url)

    def _response_format(self, schema: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "json_schema",
            "json_schema": {"name": "kivi_response", "strict": True, "schema": schema},
        }

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int = 4096,
        effort: str = "medium",
    ) -> LLMResult:
        started = time.perf_counter()
        response = None
        last_error: Exception | None = None

        # Not every OpenAI-compatible endpoint implements strict `json_schema`.
        # Try it, then fall back to plain JSON mode with the schema described in
        # the prompt - `parse_json_object` is tolerant enough to cope either way.
        for response_format in (self._response_format(schema), {"type": "json_object"}):
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    max_completion_tokens=max_tokens,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    response_format=response_format,
                )
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                if response_format.get("type") == "json_object":
                    break

        if response is None:
            return LLMResult(
                error=f"{type(last_error).__name__}: {last_error}",
                usage=LLMUsage(
                    provider=self.name,
                    model=self.model,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    calls=1,
                ),
            )

        latency_ms = (time.perf_counter() - started) * 1000
        text = response.choices[0].message.content or ""
        usage_obj = getattr(response, "usage", None)
        input_tokens = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage_obj, "completion_tokens", 0) or 0)

        return LLMResult(
            data=parse_json_object(text),
            text=text,
            usage=LLMUsage(
                provider=self.name,
                model=self.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                cost_usd=price(self.model, input_tokens, output_tokens),
                calls=1,
            ),
        )


# ---------------------------------------------------------------------------
# Groq
# ---------------------------------------------------------------------------
class GroqProvider(LLMProvider):
    """Groq, via its own SDK.

    Groq runs open-weight models on its own inference hardware, so the same
    request returns far faster and far cheaper than a frontier model. That suits
    this pipeline: extraction is one call per dictation across a 500-record
    corpus, and it is a classification-shaped task rather than one needing a
    frontier model's reasoning.

    Groq also speaks the OpenAI dialect, so `OpenAIProvider` with a `base_url`
    would in principle work. It uses the dedicated `groq` SDK instead because
    the OpenAI SDK and the Anthropic SDK currently pull in incompatible HTTP
    stacks (`httpx` vs `httpx2`), and having both installed makes OpenAI-client
    requests fail with a recursion error. The dedicated client avoids the clash.
    """

    name = "groq"

    def __init__(self, model: str | None = None) -> None:
        from groq import Groq  # optional dependency

        self.model = model or "openai/gpt-oss-120b"
        key = os.getenv("GROQ_API_KEY")
        if not key:
            raise RuntimeError("GROQ_API_KEY is not set")
        self._client = Groq(api_key=key, max_retries=3)

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int = 4096,
        effort: str = "medium",
    ) -> LLMResult:
        started = time.perf_counter()
        response = None
        last_error: Exception | None = None

        # Strict `json_schema` is what pins the response to the shape the
        # pipeline expects. Plain JSON mode is the fallback for models that do
        # not implement it - it returns valid JSON of *some* shape, which
        # `parse_json_object` and the engine's validation then have to survive.
        formats = [
            {
                "type": "json_schema",
                "json_schema": {"name": "kivi_response", "strict": True, "schema": schema},
            },
            {"type": "json_object"},
        ]
        for response_format in formats:
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    max_completion_tokens=max_tokens,
                    response_format=response_format,
                )
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                if response_format["type"] == "json_object":
                    break

        if response is None:
            return LLMResult(
                error=f"{type(last_error).__name__}: {last_error}",
                usage=LLMUsage(
                    provider=self.name,
                    model=self.model,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    calls=1,
                ),
            )

        latency_ms = (time.perf_counter() - started) * 1000
        text = response.choices[0].message.content or ""
        usage_obj = getattr(response, "usage", None)
        input_tokens = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage_obj, "completion_tokens", 0) or 0)

        return LLMResult(
            data=parse_json_object(text),
            text=text,
            usage=LLMUsage(
                provider=self.name,
                model=self.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                cost_usd=price(self.model, input_tokens, output_tokens),
                calls=1,
            ),
        )


# ---------------------------------------------------------------------------
# Google Gemini
# ---------------------------------------------------------------------------
class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, model: str | None = None) -> None:
        from google import genai  # optional dependency
        from google.genai import types

        self._types = types
        self.model = model or "gemini-flash-lite-latest"
        self._client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int = 4096,
        effort: str = "medium",
    ) -> LLMResult:
        started = time.perf_counter()
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=user,
                config=self._types.GenerateContentConfig(
                    system_instruction=system,
                    max_output_tokens=max_tokens,
                    response_mime_type="application/json",
                    response_schema=schema,
                ),
            )
        except Exception as exc:
            return LLMResult(
                error=f"{type(exc).__name__}: {exc}",
                usage=LLMUsage(
                    provider=self.name,
                    model=self.model,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    calls=1,
                ),
            )

        latency_ms = (time.perf_counter() - started) * 1000
        text = response.text or ""
        meta = getattr(response, "usage_metadata", None)
        input_tokens = int(getattr(meta, "prompt_token_count", 0) or 0)
        output_tokens = int(getattr(meta, "candidates_token_count", 0) or 0)
        if not input_tokens:
            input_tokens = estimate_tokens(system) + estimate_tokens(user)
        if not output_tokens:
            output_tokens = estimate_tokens(text)

        return LLMResult(
            data=parse_json_object(text),
            text=text,
            usage=LLMUsage(
                provider=self.name,
                model=self.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                cost_usd=price(self.model, input_tokens, output_tokens),
                calls=1,
            ),
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
_PROVIDERS = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "groq": GroqProvider,
    "gemini": GeminiProvider,
}


def build_provider(provider: str | None = None, model: str | None = None) -> LLMProvider | None:
    """Instantiate a provider, or return None if it is unavailable.

    Returning None rather than raising is deliberate: the caller falls back to
    the offline heuristic engine so a missing key never breaks a reviewer's run.
    """
    settings = get_settings()
    provider = (provider or settings.llm_provider).lower()
    model = model or settings.llm_model

    if provider in ("heuristic", "none", "offline", ""):
        return None

    factory = _PROVIDERS.get(provider)
    if factory is None:
        print(f"[kivi] unknown LLM provider '{provider}'; using the offline heuristic engine.")
        return None

    try:
        return factory(model=model or None)
    except Exception as exc:
        print(
            f"[kivi] LLM provider '{provider}' unavailable ({type(exc).__name__}: {exc}); "
            f"using the offline heuristic engine instead."
        )
        return None
