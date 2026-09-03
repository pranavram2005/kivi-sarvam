"""The reasoning engine: the three model-driven decisions, behind one interface.

Two implementations satisfy it:

  * `LLMEngine`       - drives a real model (Claude / GPT / Gemini) with the
                        prompts and JSON schemas in `prompts.py`.
  * `HeuristicEngine` - a deterministic rule engine that needs no API key, so
                        the whole product and its evaluation can be run offline.

Everything downstream - extraction, resolution, retrieval, answering, the API,
the evaluation harness - talks to this interface and never to a provider
directly. Swapping the model is a one-line change in `.env`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backend.config import get_settings
from backend.llm.base import LLMProvider, LLMUsage
from backend.llm.prompts import (
    ANSWER_SCHEMA,
    ANSWER_SYSTEM,
    EXTRACTION_SCHEMA,
    EXTRACTION_SYSTEM,
    MEMORY_TYPES,
    RESOLUTION_SCHEMA,
    RESOLUTION_SYSTEM,
    answer_user_prompt,
    extraction_user_prompt,
    resolution_user_prompt,
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
@dataclass
class ExtractedMemory:
    """One candidate memory, before it has been reconciled with what we know."""

    type: str = "episode"
    content: str = ""
    subject: str | None = None
    attribute: str | None = None
    value: str | None = None
    entities: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.5
    occurred_at: str | None = None

    # The sentence this memory came from, before any tidying. `content` is
    # rewritten to read well six weeks later ("Meeting with Priya is on Thursday
    # at 4 PM"), which strips the very words - "Beacon onboarding review" - that
    # tell the resolver which appointment is being corrected. The original is
    # kept for that comparison and is never shown to the user.
    source_sentence: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "content": self.content,
            "subject": self.subject,
            "attribute": self.attribute,
            "value": self.value,
            "entities": self.entities,
            "tags": self.tags,
            "confidence": self.confidence,
            "occurred_at": self.occurred_at,
            "source_sentence": self.source_sentence or self.content,
        }


@dataclass
class ExtractionResult:
    decision: str = "IGNORE"  # REMEMBER | IGNORE
    rationale: str = ""
    memories: list[ExtractedMemory] = field(default_factory=list)
    usage: LLMUsage = field(default_factory=LLMUsage)
    raw: str = ""


@dataclass
class ResolutionDecision:
    action: str = "NEW"  # NEW | DUPLICATE | SUPERSEDES | CONFLICTS
    target_memory_id: int | None = None
    reason: str = ""
    usage: LLMUsage = field(default_factory=LLMUsage)


@dataclass
class AnswerResult:
    answer: str = ""
    used_memory_ids: list[int] = field(default_factory=list)
    abstained: bool = False
    conflict: bool = False
    supported: bool = True
    confidence: float = 0.0
    reasoning: str = ""
    usage: LLMUsage = field(default_factory=LLMUsage)


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------
class ReasoningEngine(ABC):
    """The three decisions Kivi's memory system needs a mind for."""

    name: str = "base"
    model: str = "unknown"

    @abstractmethod
    def extract(
        self, *, formatted_text: str, raw_asr: str, timestamp: str, application: str | None
    ) -> ExtractionResult:
        """Decide what, if anything, in one dictation is worth remembering."""

    @abstractmethod
    def resolve(
        self, *, new_memory: dict[str, Any], candidates: list[dict[str, Any]]
    ) -> ResolutionDecision:
        """Decide how a new memory relates to the ones already stored."""

    @abstractmethod
    def answer(
        self, *, question: str, memories: list[dict[str, Any]], plan: Any, now: str | None = None
    ) -> AnswerResult:
        """Answer a Hey Kivi question from retrieved memory, or abstain."""

    def describe(self) -> dict[str, str]:
        return {"engine": self.name, "model": self.model}


# ---------------------------------------------------------------------------
# LLM-backed engine
# ---------------------------------------------------------------------------
def _clamp(value: Any, low: float, high: float, default: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


def _clean_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


class LLMEngine(ReasoningEngine):
    """Drives a real model, then validates and clamps whatever comes back.

    A model can still return a memory type we do not recognise or a confidence
    of 12.0. Everything is normalised here so the rest of the system can trust
    its inputs.
    """

    name = "llm"

    def __init__(self, provider: LLMProvider, fallback: ReasoningEngine | None = None) -> None:
        self.provider = provider
        self.name = provider.name
        self.model = provider.model
        # If a call fails (network, rate limit, refusal), the deterministic
        # engine takes over for that one item rather than losing the record.
        self.fallback = fallback

    # -- extraction --------------------------------------------------------
    def extract(
        self, *, formatted_text: str, raw_asr: str, timestamp: str, application: str | None
    ) -> ExtractionResult:
        result = self.provider.complete_json(
            system=EXTRACTION_SYSTEM,
            user=extraction_user_prompt(
                formatted_text=formatted_text,
                raw_asr=raw_asr,
                timestamp=timestamp,
                application=application,
            ),
            schema=EXTRACTION_SCHEMA,
            max_tokens=2048,
            # Extraction runs once per transcript across a ~500 record corpus.
            # It is a classification-shaped task, so it runs at low effort.
            effort="low",
        )

        if result.error or not result.data:
            if self.fallback is not None:
                fallback_result = self.fallback.extract(
                    formatted_text=formatted_text,
                    raw_asr=raw_asr,
                    timestamp=timestamp,
                    application=application,
                )
                fallback_result.rationale = (
                    f"[fell back to the offline engine: {result.error or 'empty response'}] "
                    + fallback_result.rationale
                )
                fallback_result.usage = result.usage
                return fallback_result
            return ExtractionResult(
                decision="IGNORE",
                rationale=f"extraction failed: {result.error}",
                usage=result.usage,
            )

        data = result.data
        memories: list[ExtractedMemory] = []
        for item in data.get("memories") or []:
            if not isinstance(item, dict):
                continue
            content = _clean_str(item.get("content"))
            if not content:
                continue
            memory_type = str(item.get("type") or "episode").lower()
            if memory_type not in MEMORY_TYPES:
                memory_type = "episode"
            memories.append(
                ExtractedMemory(
                    type=memory_type,
                    content=content,
                    subject=_clean_str(item.get("subject")),
                    attribute=_clean_str(item.get("attribute")),
                    value=_clean_str(item.get("value")),
                    entities=[
                        e.strip()
                        for e in (item.get("entities") or [])
                        if isinstance(e, str) and e.strip()
                    ],
                    tags=[
                        t.strip().lower()
                        for t in (item.get("tags") or [])
                        if isinstance(t, str) and t.strip()
                    ],
                    confidence=_clamp(item.get("confidence"), 0.0, 1.0, 0.5),
                    occurred_at=_clean_str(item.get("occurred_at")),
                    # The words the user actually spoke, kept for the resolver.
                    # `content` has been rewritten to stand on its own in six
                    # weeks' time, and that rewriting is exactly what removes
                    # "Actually" and "moved to" - the signals that separate a
                    # correction from a second, unrelated appointment. The
                    # heuristic engine has always kept this; without it here,
                    # every LLM-extracted correction reached the resolver
                    # already laundered of the evidence it needed.
                    source_sentence=formatted_text,
                )
            )

        decision = str(data.get("decision") or "").upper()
        if decision not in ("REMEMBER", "IGNORE"):
            decision = "REMEMBER" if memories else "IGNORE"
        if not memories:
            decision = "IGNORE"

        return ExtractionResult(
            decision=decision,
            rationale=str(data.get("rationale") or ""),
            memories=memories,
            usage=result.usage,
            raw=result.text,
        )

    # -- resolution --------------------------------------------------------
    def resolve(
        self, *, new_memory: dict[str, Any], candidates: list[dict[str, Any]]
    ) -> ResolutionDecision:
        if not candidates:
            return ResolutionDecision(action="NEW", reason="no memory occupies this slot yet")

        result = self.provider.complete_json(
            system=RESOLUTION_SYSTEM,
            user=resolution_user_prompt(new_memory=new_memory, candidates=candidates),
            schema=RESOLUTION_SCHEMA,
            max_tokens=512,
            effort="low",
        )

        if result.error or not result.data:
            if self.fallback is not None:
                decision = self.fallback.resolve(new_memory=new_memory, candidates=candidates)
                decision.reason = f"[offline fallback] {decision.reason}"
                decision.usage = result.usage
                return decision
            return ResolutionDecision(
                action="NEW", reason=f"resolution failed: {result.error}", usage=result.usage
            )

        action = str(result.data.get("action") or "NEW").upper()
        if action not in ("NEW", "DUPLICATE", "SUPERSEDES", "CONFLICTS"):
            action = "NEW"

        raw_target = result.data.get("target_memory_id") or 0
        try:
            target = int(raw_target)
        except (TypeError, ValueError):
            target = 0
        valid_ids = {c["id"] for c in candidates}
        target_id = target if target in valid_ids else None
        if action != "NEW" and target_id is None:
            # The model named an id we did not offer; fall back to the most
            # recent candidate rather than acting on nothing.
            target_id = candidates[0]["id"]

        return ResolutionDecision(
            action=action,
            target_memory_id=target_id,
            reason=str(result.data.get("reason") or ""),
            usage=result.usage,
        )

    # -- answering ---------------------------------------------------------
    def answer(
        self, *, question: str, memories: list[dict[str, Any]], plan: Any, now: str | None = None
    ) -> AnswerResult:
        now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")

        result = self.provider.complete_json(
            system=ANSWER_SYSTEM,
            user=answer_user_prompt(question=question, memories=memories, now=now),
            schema=ANSWER_SCHEMA,
            max_tokens=1500,
            effort="medium",
        )

        if result.error or not result.data:
            if self.fallback is not None:
                answer = self.fallback.answer(
                    question=question, memories=memories, plan=plan, now=now
                )
                answer.reasoning = f"[offline fallback] {answer.reasoning}"
                answer.usage = result.usage
                return answer
            return AnswerResult(
                answer="I ran into a problem reaching the language model, so I can't answer "
                "that right now.",
                abstained=True,
                supported=False,
                reasoning=f"answering failed: {result.error}",
                usage=result.usage,
            )

        data = result.data
        offered = {m["id"] for m in memories}
        used = [
            int(mid)
            for mid in (data.get("used_memory_ids") or [])
            if isinstance(mid, (int, float)) and int(mid) in offered
        ]

        abstained = bool(data.get("abstained"))
        if abstained:
            used = []

        return AnswerResult(
            answer=str(data.get("answer") or "").strip(),
            used_memory_ids=used,
            abstained=abstained,
            conflict=bool(data.get("conflict")),
            supported=bool(data.get("supported")) and not abstained,
            confidence=_clamp(data.get("confidence"), 0.0, 1.0, 0.5),
            reasoning=str(data.get("reasoning") or ""),
            usage=result.usage,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
_engine: ReasoningEngine | None = None


def build_engine(provider_name: str | None = None, model: str | None = None) -> ReasoningEngine:
    """Build the configured engine, falling back to the offline one.

    Import is local to avoid a circular import: the heuristic engine imports
    the result types defined above.
    """
    from backend.llm.heuristic import HeuristicEngine
    from backend.llm.providers import build_provider

    heuristic = HeuristicEngine()
    provider = build_provider(provider_name, model)
    if provider is None:
        return heuristic
    return LLMEngine(provider, fallback=heuristic)


def get_engine(refresh: bool = False) -> ReasoningEngine:
    global _engine
    if _engine is None or refresh:
        settings = get_settings()
        _engine = build_engine(settings.llm_provider, settings.llm_model)
    return _engine
