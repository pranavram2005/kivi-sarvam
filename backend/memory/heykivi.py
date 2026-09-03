"""Hey Kivi: question in, grounded answer out, with its provenance attached.

    question
      -> plan_query()        what is being asked, about whom, about what
      -> retrieve()          the handful of memories that could bear on it
      -> engine.answer()     an answer, an abstention, or a flagged conflict
      -> verify()            does the answer actually cite what it used?
      -> log + provenance    memories used -> source transcripts -> the words
                             the user originally said

The verification step matters: a model can produce a fluent answer and then
list the wrong memory ids, or none at all. When the citation does not hold up,
the answer is marked unsupported rather than quietly presented as grounded.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backend.config import get_settings
from backend.llm.engine import ReasoningEngine, get_engine
from backend.memory import store
from backend.memory.retriever import retrieve, retrieve_transcripts, to_answer_context
from backend.memory.text import content_tokens, normalise


@dataclass
class Source:
    """One link in the provenance chain: memory -> transcript -> spoken words."""

    memory_id: int
    memory_content: str
    memory_type: str
    status: str
    confidence: float
    transcript_id: int | None
    timestamp: str | None
    application: str | None
    excerpt: str | None
    score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "memory_content": self.memory_content,
            "memory_type": self.memory_type,
            "status": self.status,
            "confidence": self.confidence,
            "transcript_id": self.transcript_id,
            "timestamp": self.timestamp,
            "application": self.application,
            "excerpt": self.excerpt,
            "score": self.score,
        }


@dataclass
class HeyKiviAnswer:
    question: str
    answer: str
    abstained: bool = False
    conflict: bool = False
    supported: bool = True
    confidence: float = 0.0
    reasoning: str = ""
    intent: str = "general"
    entities: list[str] = field(default_factory=list)

    retrieved_memory_ids: list[int] = field(default_factory=list)
    used_memory_ids: list[int] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    retrieval_detail: list[dict[str, Any]] = field(default_factory=list)

    provider: str = "heuristic"
    model: str = "heuristic"
    memories_considered: int = 0
    retrieval_latency_ms: float = 0.0
    llm_latency_ms: float = 0.0
    total_latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    query_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "question": self.question,
            "answer": self.answer,
            "abstained": self.abstained,
            "conflict": self.conflict,
            "supported": self.supported,
            "confidence": round(self.confidence, 3),
            "reasoning": self.reasoning,
            "intent": self.intent,
            "entities": self.entities,
            "retrieved_memory_ids": self.retrieved_memory_ids,
            "used_memory_ids": self.used_memory_ids,
            "sources": [s.as_dict() for s in self.sources],
            "retrieval_detail": self.retrieval_detail,
            "diagnostics": {
                "provider": self.provider,
                "model": self.model,
                "memories_considered": self.memories_considered,
                "memories_retrieved": len(self.retrieved_memory_ids),
                "memories_used": len(self.used_memory_ids),
                "retrieval_latency_ms": round(self.retrieval_latency_ms, 2),
                "llm_latency_ms": round(self.llm_latency_ms, 2),
                "total_latency_ms": round(self.total_latency_ms, 2),
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "total_tokens": self.input_tokens + self.output_tokens,
                "estimated_cost_usd": round(self.cost_usd, 6),
            },
        }


def _verify_support(answer_text: str, used: list[dict[str, Any]]) -> tuple[bool, str]:
    """A cheap, honest check that the answer is actually made of the memories.

    Not a semantic entailment test - it measures how much of the answer's
    content vocabulary appears in the cited memories. A fluent answer citing
    memories that share almost no vocabulary with it is a citation that does not
    hold up, and is reported as unsupported rather than trusted.
    """
    answer_tokens = set(content_tokens(answer_text))
    if not answer_tokens:
        return False, "the answer was empty"
    if not used:
        return False, "the answer cited no memories"

    # An answer that quotes its evidence verbatim is supported by definition.
    # Provenance answers do exactly that - "Because of what you dictated on
    # Friday: '...'" - and then fail a bag-of-words overlap test, because the
    # citation scaffolding around the quote dilutes it.
    normalised_answer = normalise(answer_text)
    for memory in used:
        for span in (memory.get("content"), memory.get("source_excerpt")):
            if span and len(span) > 20 and normalise(span).rstrip(".") in normalised_answer:
                return True, "the answer quotes a cited memory verbatim"

    memory_tokens: set[str] = set()
    for memory in used:
        memory_tokens.update(content_tokens(memory.get("content") or ""))
        memory_tokens.update(content_tokens(memory.get("value") or ""))
        memory_tokens.update(content_tokens(memory.get("subject") or ""))

    overlap = len(answer_tokens & memory_tokens) / len(answer_tokens)
    if overlap >= 0.34:
        return True, f"{overlap:.0%} of the answer's content words come from the cited memories"
    return (
        False,
        f"only {overlap:.0%} of the answer's content words appear in the cited memories",
    )


def ask(
    question: str,
    *,
    user_id: str | None = None,
    engine: ReasoningEngine | None = None,
    top_k: int | None = None,
    persist: bool = True,
) -> HeyKiviAnswer:
    """Answer one Hey Kivi question from stored memory."""
    settings = get_settings()
    engine = engine or get_engine()
    user_id = user_id or settings.default_user_id
    started = time.perf_counter()

    retrieval = retrieve(question, user_id=user_id, top_k=top_k, settings=settings)
    context = to_answer_context(retrieval)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    llm_started = time.perf_counter()
    result = engine.answer(question=question, memories=context, plan=retrieval.plan, now=now)

    # --- the rescue -------------------------------------------------------
    # Memories answer; transcripts only rescue. The trigger is ABSTENTION, not
    # empty retrieval: retrieval nearly always returns something, and the real
    # failure is retrieving eight near-misses and then honestly refusing. When
    # that happens the answer may still be sitting in a dictation that never
    # became a memory - the extractor ignored it, or rejected it below the
    # confidence threshold.
    #
    # Running this only on abstention is what keeps reconciliation intact. A
    # superseded transcript can never appear beside the memory that replaced it,
    # because whenever memories answered we never look at transcripts at all.
    fell_back = False
    if result.abstained and not result.conflict:
        rescued = retrieve_transcripts(question, user_id=user_id, settings=settings)
        if rescued:
            second = engine.answer(
                question=question, memories=rescued, plan=retrieval.plan, now=now
            )
            # Keep the rescue only if it actually produced an answer; a second
            # abstention means the content genuinely is not there, and the
            # honest refusal is the better result.
            if not second.abstained:
                context, result, fell_back = rescued, second, True

    llm_latency_ms = (time.perf_counter() - llm_started) * 1000

    by_id = {m["id"]: m for m in context}
    used = [by_id[i] for i in result.used_memory_ids if i in by_id]

    supported = result.supported
    reasoning = result.reasoning
    # A conflict answer is a statement *about* the memories ("I found two
    # different times..."), so it deliberately shares little vocabulary with
    # them. What matters there is that it cites the memories it is describing.
    if not result.abstained and not result.conflict:
        verified, note = _verify_support(result.answer, used)
        if not verified:
            supported = False
            reasoning = f"{reasoning} (support check: {note})".strip()
    elif result.conflict:
        supported = bool(used)
        if not used:
            reasoning = f"{reasoning} (support check: a conflict was reported with no memories cited)".strip()

    sources = [
        Source(
            memory_id=memory["id"],
            memory_content=memory["content"],
            memory_type=memory["type"],
            status=memory["status"],
            confidence=float(memory.get("confidence") or 0),
            transcript_id=memory.get("source_transcript_id"),
            timestamp=memory.get("timestamp"),
            application=memory.get("application"),
            excerpt=memory.get("source_excerpt"),
            score=float(memory.get("score") or 0),
        )
        for memory in used
    ]

    total_latency_ms = (time.perf_counter() - started) * 1000

    answer = HeyKiviAnswer(
        question=question,
        answer=result.answer,
        abstained=result.abstained,
        conflict=result.conflict,
        supported=supported,
        confidence=result.confidence,
        reasoning=reasoning,
        intent=retrieval.plan.intent,
        entities=retrieval.plan.entities,
        retrieved_memory_ids=retrieval.memory_ids,
        used_memory_ids=[m["id"] for m in used],
        sources=sources,
        retrieval_detail=retrieval.detail(),
        provider=result.usage.provider,
        model=result.usage.model,
        memories_considered=retrieval.considered,
        retrieval_latency_ms=retrieval.latency_ms,
        llm_latency_ms=llm_latency_ms,
        total_latency_ms=total_latency_ms,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        cost_usd=result.usage.cost_usd,
    )

    if persist:
        answer.query_id = store.log_query(
            user_id=user_id,
            question=question,
            answer=answer.answer,
            abstained=answer.abstained,
            conflict=answer.conflict,
            supported=answer.supported,
            confidence=answer.confidence,
            reasoning=answer.reasoning,
            retrieved_memory_ids=answer.retrieved_memory_ids,
            used_memory_ids=answer.used_memory_ids,
            retrieval_detail=answer.retrieval_detail,
            provider=answer.provider,
            model=answer.model,
            retrieval_latency_ms=answer.retrieval_latency_ms,
            llm_latency_ms=answer.llm_latency_ms,
            total_latency_ms=answer.total_latency_ms,
            input_tokens=answer.input_tokens,
            output_tokens=answer.output_tokens,
            cost_usd=answer.cost_usd,
        )

    return answer


def provenance_chain(memory_id: int) -> dict[str, Any]:
    """Walk one memory back to the words that produced it."""
    memory = store.get_memory(memory_id)
    if memory is None:
        return {}
    transcript = (
        store.get_transcript(memory["source_transcript_id"])
        if memory.get("source_transcript_id")
        else None
    )
    return {
        "memory": memory,
        "transcript": transcript,
        "events": store.events_for(memory_id),
        "relations": store.relations_for(memory_id),
    }
