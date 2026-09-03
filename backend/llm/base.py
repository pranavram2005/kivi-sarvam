"""Provider-agnostic LLM plumbing: usage accounting, cost, and the base class."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from backend.config import get_settings


@dataclass
class LLMUsage:
    """What one model call cost, in tokens, milliseconds and dollars."""

    provider: str = "heuristic"
    model: str = "heuristic"
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    calls: int = 0

    def add(self, other: "LLMUsage") -> "LLMUsage":
        return LLMUsage(
            provider=other.provider or self.provider,
            model=other.model or self.model,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            latency_ms=self.latency_ms + other.latency_ms,
            cost_usd=self.cost_usd + other.cost_usd,
            calls=self.calls + other.calls,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": round(self.latency_ms, 2),
            "cost_usd": round(self.cost_usd, 6),
            "calls": self.calls,
        }


def price(model: str, input_tokens: int, output_tokens: int) -> float:
    """Dollar cost of a call, from the price table in `backend/config.py`."""
    in_rate, out_rate = get_settings().price_for(model)
    return (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate


@dataclass
class LLMResult:
    """A single structured completion."""

    data: dict[str, Any] = field(default_factory=dict)
    text: str = ""
    usage: LLMUsage = field(default_factory=LLMUsage)
    error: str | None = None


class LLMProvider(ABC):
    """A model that can be asked for JSON matching a schema."""

    name: str = "base"
    model: str = "unknown"

    @abstractmethod
    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int = 4096,
        effort: str = "medium",
    ) -> LLMResult:
        ...

    def describe(self) -> dict[str, str]:
        return {"provider": self.name, "model": self.model}


# ---------------------------------------------------------------------------
# Tolerant JSON parsing
# ---------------------------------------------------------------------------
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json_object(text: str) -> dict[str, Any]:
    """Pull a JSON object out of a model response.

    Structured-output modes guarantee clean JSON, but this stays tolerant of
    fenced blocks and leading prose so that a provider without a strict JSON
    mode still works.
    """
    if not text:
        return {}
    candidate = text.strip()

    fenced = _FENCE_RE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()

    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except ValueError:
        pass

    # Fall back to the outermost balanced { ... } span.
    start = candidate.find("{")
    if start == -1:
        return {}
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(candidate)):
        char = candidate[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(candidate[start : index + 1])
                    return parsed if isinstance(parsed, dict) else {}
                except ValueError:
                    return {}
    return {}
