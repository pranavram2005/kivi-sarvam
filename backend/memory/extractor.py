"""Turning a dictation into durable memory.

The pipeline for one transcript:

    transcript
        -> engine.extract()            what, if anything, is worth remembering
        -> confidence gate             low-confidence guesses become REJECTED
        -> slot lookup                 what do we already believe about this?
        -> engine.resolve()            NEW / DUPLICATE / SUPERSEDES / CONFLICTS
        -> write memory + relation + event
        -> mark transcript processed

Nothing is deleted along the way. A rejected memory is stored with status
REJECTED and a superseded one keeps its row and gains a pointer to its
replacement, so the Inspector can show what Kivi chose *not* to believe and why.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.config import get_settings
from backend.llm.embeddings import get_embedder
from backend.llm.engine import ExtractedMemory, ExtractionResult, ReasoningEngine, get_engine
from backend.memory import store
from backend.memory.trace import NULL as NULL_TRACER, Tracer
from backend.memory.store import ACTIVE, REJECTED, SUPERSEDED


@dataclass
class MemoryOutcome:
    """What happened to one candidate memory."""

    action: str  # CREATED | REJECTED | DUPLICATE | SUPERSEDED | CONFLICT
    memory_id: int | None
    content: str
    memory_type: str
    confidence: float
    reason: str
    target_memory_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "memory_id": self.memory_id,
            "content": self.content,
            "type": self.memory_type,
            "confidence": self.confidence,
            "reason": self.reason,
            "target_memory_id": self.target_memory_id,
        }


@dataclass
class ProcessResult:
    """The full record of processing one transcript."""

    transcript_id: int
    decision: str
    rationale: str
    outcomes: list[MemoryOutcome] = field(default_factory=list)
    provider: str = "heuristic"
    model: str = "heuristic"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0

    @property
    def created(self) -> int:
        return sum(1 for o in self.outcomes if o.action == "CREATED")

    @property
    def rejected(self) -> int:
        return sum(1 for o in self.outcomes if o.action == "REJECTED")

    @property
    def superseded(self) -> int:
        return sum(1 for o in self.outcomes if o.action == "SUPERSEDED")

    @property
    def duplicates(self) -> int:
        return sum(1 for o in self.outcomes if o.action == "DUPLICATE")

    @property
    def conflicts(self) -> int:
        return sum(1 for o in self.outcomes if o.action == "CONFLICT")

    def as_dict(self) -> dict[str, Any]:
        return {
            "transcript_id": self.transcript_id,
            "decision": self.decision,
            "rationale": self.rationale,
            "outcomes": [o.as_dict() for o in self.outcomes],
            "created": self.created,
            "rejected": self.rejected,
            "superseded": self.superseded,
            "duplicates": self.duplicates,
            "conflicts": self.conflicts,
            "provider": self.provider,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "latency_ms": round(self.latency_ms, 2),
        }


def _embedding_text(memory: ExtractedMemory) -> str:
    """What actually gets embedded.

    The subject and entities are repeated alongside the sentence so that a
    question naming a person reliably reaches memories about that person, even
    when the sentence itself phrases the name differently.
    """
    parts = [memory.content]
    if memory.subject:
        parts.append(memory.subject)
    parts.extend(memory.entities)
    if memory.attribute:
        parts.append(memory.attribute.replace("_", " "))
    if memory.value:
        parts.append(memory.value)
    parts.extend(memory.tags)
    return " ".join(parts)


def process_transcript(
    transcript: dict[str, Any],
    *,
    user_id: str | None = None,
    engine: ReasoningEngine | None = None,
    settings=None,
    extraction: "ExtractionResult | None" = None,
    tracer: Tracer | None = None,
) -> ProcessResult:
    """Extract, reconcile and store the memories in one transcript.

    `extraction` lets a caller supply a result it already obtained - see
    `process_pending`, which computes them concurrently and then replays them
    through here in order.
    """
    settings = settings or get_settings()
    engine = engine or get_engine()
    embedder = get_embedder()
    user_id = user_id or transcript.get("user_id") or settings.default_user_id
    transcript_id = int(transcript["id"])

    started = time.perf_counter()
    trace = tracer or NULL_TRACER
    text = transcript.get("formatted_text") or ""
    trace.stage(
        "dictation",
        "Store the words, unchanged",
        "The dictation is written down exactly as it was said before anything reads it. "
        "Nothing downstream can alter or lose the original.",
        facts=[
            ("dictation", f"#{transcript_id}"),
            ("length", f"{len(text)} characters, {len(text.split())} words"),
            ("application", transcript.get("application") or "-"),
            ("recognised as", (transcript.get("raw_asr") or "-")[:80]),
        ],
        note="Kept verbatim because it is the bottom of every provenance chain - the "
        "answer you eventually get has to be traceable back to these words.",
    )

    trace.begin(
        "extract",
        "Decide what is worth keeping",
        "Reading the dictation for durable claims, and deciding whether there are any.",
    )
    if extraction is None:
        extraction = engine.extract(
            formatted_text=text,
            raw_asr=transcript.get("raw_asr") or "",
            timestamp=transcript.get("timestamp") or "",
            application=transcript.get("application"),
        )
    trace.stage(
        "extract",
        "Decide what is worth keeping",
        "Most dictation is transient - a draft email is not a fact about you. This "
        "separates the durable claims from the text around them, or decides there are "
        "none.",
        facts=[
            ("verdict", extraction.decision),
            ("candidates found", len(extraction.memories)),
            ("decided by", f"{extraction.usage.provider} - {extraction.usage.model}"),
            ("reason", (extraction.rationale or "-")[:140]),
        ],
        table={
            "head": ["candidate", "type", "about", "confidence"],
            "rows": [
                [
                    (candidate.content or "")[:64],
                    candidate.type,
                    candidate.subject or "-",
                    f"{candidate.confidence:.2f}",
                ]
                for candidate in extraction.memories
            ],
        },
        note="This is the one stage where a configured model changes the result "
        "measurably: on phrasing the rules were never tuned on, recall goes from 62% "
        "to 97%. Everything after this is identical either way.",
    )

    result = ProcessResult(
        transcript_id=transcript_id,
        decision=extraction.decision,
        rationale=extraction.rationale,
        provider=extraction.usage.provider,
        model=extraction.usage.model,
        input_tokens=extraction.usage.input_tokens,
        output_tokens=extraction.usage.output_tokens,
        cost_usd=extraction.usage.cost_usd,
    )

    if extraction.decision == "IGNORE" or not extraction.memories:
        store.log_event(
            memory_id=None,
            transcript_id=transcript_id,
            event="IGNORED",
            reason=extraction.rationale or "nothing durable in this dictation",
        )
    else:
        for candidate in extraction.memories:
            trace.mark()
            outcome = _store_one(
                candidate,
                transcript=transcript,
                user_id=user_id,
                engine=engine,
                embedder=embedder,
                settings=settings,
                result=result,
                tracer=trace,
            )
            result.outcomes.append(outcome)

    if extraction.memories:
        trace.stage(
            "stored",
            "Write it down, and keep the audit",
            "Every decision above is recorded as an event against the memory it "
            "concerns, so what Kivi believes can always be explained by what it was "
            "told.",
            facts=[
                ("learned", result.created or None),
                ("corrected an earlier memory", result.superseded or None),
                ("already knew", result.duplicates or None),
                ("not confident enough", result.rejected or None),
            ],
            note="Nothing is deleted here, at any point. A rejected candidate is stored "
            "as REJECTED and a corrected memory as SUPERSEDED - both stay visible and "
            "both stay explainable.",
        )

    result.latency_ms = (time.perf_counter() - started) * 1000

    store.log_extraction_run(
        transcript_id=transcript_id,
        provider=result.provider,
        model=result.model,
        decision=result.decision,
        rationale=result.rationale,
        created=result.created,
        rejected=result.rejected,
        superseded=result.superseded,
        duplicate=result.duplicates,
        raw_response=extraction.raw or None,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        latency_ms=result.latency_ms,
        cost_usd=result.cost_usd,
    )
    store.mark_transcript_processed(transcript_id)
    return result


def _store_one(
    candidate: ExtractedMemory,
    *,
    transcript: dict[str, Any],
    user_id: str,
    engine: ReasoningEngine,
    embedder,
    settings,
    result: ProcessResult,
    tracer: Tracer | None = None,
) -> MemoryOutcome:
    transcript_id = int(transcript["id"])
    trace = tracer or NULL_TRACER
    embedded_text = _embedding_text(candidate)
    vector = embedder.embed(embedded_text)
    trace.stage(
        "embed",
        "Turn it into a vector",
        "The same hashing used on a question at retrieval time: words, word pairs and "
        "four-character runs into a fixed set of buckets, normalised. Stored beside the "
        "memory so a later question can be compared against it in one dot product.",
        facts=[
            ("embedded", embedded_text[:90]),
            ("model", f"{embedder.name} - {embedder.model}"),
            ("dimensions", len(vector)),
            ("buckets used", f"{sum(1 for v in vector if v)} of {len(vector)}"),
        ],
        note="No API call, no model download, no GPU - which is why the whole 500-record "
        "corpus can be indexed offline in seconds.",
    )

    # ---- confidence gate -------------------------------------------------
    # A low-confidence extraction is still written down, as REJECTED, so that a
    # reviewer can see what Kivi decided not to trust. It is never retrieved.
    if candidate.confidence < settings.min_memory_confidence:
        memory_id = store.insert_memory(
            user_id=user_id,
            memory_type=candidate.type,
            content=candidate.content,
            subject=candidate.subject,
            attribute=candidate.attribute,
            value=candidate.value,
            entities=candidate.entities,
            tags=candidate.tags,
            confidence=candidate.confidence,
            status=REJECTED,
            source_transcript_id=transcript_id,
            occurred_at=candidate.occurred_at,
            embedding=vector,
            embedding_model=embedder.model,
        )
        reason = (
            f"confidence {candidate.confidence:.2f} is below the "
            f"{settings.min_memory_confidence:.2f} threshold"
        )
        store.log_event(
            memory_id=memory_id,
            transcript_id=transcript_id,
            event="REJECTED",
            reason=reason,
            detail=candidate.as_dict(),
        )
        trace.stage(
            "reject",
            "Not confident enough - written down anyway",
            "A candidate below the confidence threshold is never retrieved, but it is "
            "still stored, as REJECTED.",
            facts=[
                ("candidate", (candidate.content or "")[:80]),
                ("confidence", f"{candidate.confidence:.2f}"),
                ("threshold", f"{settings.min_memory_confidence:.2f}"),
                ("stored as", f"REJECTED, memory #{memory_id}"),
            ],
            note="Storing it is the point. A reviewer can see what Kivi decided not to "
            "trust, which a system that quietly drops low-confidence extractions cannot "
            "show you.",
        )
        return MemoryOutcome(
            action="REJECTED",
            memory_id=memory_id,
            content=candidate.content,
            memory_type=candidate.type,
            confidence=candidate.confidence,
            reason=reason,
        )

    # ---- does this correct something we already believe? -----------------
    candidates = store.slot_candidates(
        user_id=user_id,
        subject=candidate.subject,
        attribute=candidate.attribute,
        memory_type=candidate.type,
    )
    slot_view = [
        {
            "id": c["id"],
            "type": c["type"],
            "content": c["content"],
            "subject": c.get("subject"),
            "attribute": c.get("attribute"),
            "value": c.get("value"),
            "entities": c.get("entities") or [],
            "tags": c.get("tags") or [],
            "timestamp": c.get("timestamp") or c.get("created_at"),
            # The dictation this memory came from, for topic comparison.
            "source_sentence": c.get("source_sentence") or c["content"],
        }
        for c in candidates
    ]

    trace.begin(
        "reconcile",
        "Compare it with what is already believed",
        "New, a repeat, or a correction? Comparing the candidate against what is "
        "already stored about the same subject.",
    )
    decision = engine.resolve(
        new_memory={**candidate.as_dict(), "timestamp": transcript.get("timestamp")},
        candidates=slot_view,
    )
    result.input_tokens += decision.usage.input_tokens
    result.output_tokens += decision.usage.output_tokens
    result.cost_usd += decision.usage.cost_usd
    trace.stage(
        "reconcile",
        "Compare it with what is already believed",
        "New, a repeat, or a correction? Memories about the same subject and attribute "
        "are pulled up and compared, because moving a meeting is not the same as "
        "booking a second one.",
        facts=[
            ("about", f"{candidate.subject or '-'} / {candidate.attribute or '-'}"),
            ("existing memories in that slot", len(slot_view)),
            ("verdict", decision.action),
            (
                "replaces",
                f"memory #{decision.target_memory_id}" if decision.target_memory_id else None,
            ),
            ("reason", (decision.reason or "-")[:140]),
        ],
        table={
            "head": ["already believed", "when"],
            "rows": [
                [(c["content"] or "")[:72], (c.get("timestamp") or "-")[:16].replace("T", " ")]
                for c in slot_view[:5]
            ],
        },
        note="A correction demotes the old memory to SUPERSEDED and links the two. It "
        "does not delete it - which is what lets the history stay answerable after the "
        "belief has changed.",
    )

    # ---- duplicate: say nothing twice ------------------------------------
    if decision.action == "DUPLICATE" and decision.target_memory_id:
        store.log_event(
            memory_id=decision.target_memory_id,
            transcript_id=transcript_id,
            event="DUPLICATE_SKIPPED",
            reason=decision.reason,
            detail={"candidate": candidate.as_dict()},
        )
        return MemoryOutcome(
            action="DUPLICATE",
            memory_id=None,
            content=candidate.content,
            memory_type=candidate.type,
            confidence=candidate.confidence,
            reason=decision.reason,
            target_memory_id=decision.target_memory_id,
        )

    # ---- write the new memory --------------------------------------------
    memory_id = store.insert_memory(
        user_id=user_id,
        memory_type=candidate.type,
        content=candidate.content,
        subject=candidate.subject,
        attribute=candidate.attribute,
        value=candidate.value,
        entities=candidate.entities,
        tags=candidate.tags,
        confidence=candidate.confidence,
        status=ACTIVE,
        source_transcript_id=transcript_id,
        occurred_at=candidate.occurred_at,
        embedding=vector,
        embedding_model=embedder.model,
    )
    store.log_event(
        memory_id=memory_id,
        transcript_id=transcript_id,
        event="CREATED",
        reason=f"extracted as a {candidate.type} memory",
        detail=candidate.as_dict(),
    )

    # ---- supersede the belief it replaces --------------------------------
    if decision.action == "SUPERSEDES" and decision.target_memory_id:
        store.set_status(decision.target_memory_id, SUPERSEDED, superseded_by_id=memory_id)
        store.add_relation(
            memory_id=memory_id,
            related_memory_id=decision.target_memory_id,
            relation_type="SUPERSEDES",
            note=decision.reason,
        )
        store.log_event(
            memory_id=decision.target_memory_id,
            transcript_id=transcript_id,
            event="SUPERSEDED",
            reason=decision.reason,
            detail={"superseded_by": memory_id},
        )
        return MemoryOutcome(
            action="SUPERSEDED",
            memory_id=memory_id,
            content=candidate.content,
            memory_type=candidate.type,
            confidence=candidate.confidence,
            reason=decision.reason,
            target_memory_id=decision.target_memory_id,
        )

    # ---- record the disagreement, do not resolve it ----------------------
    if decision.action == "CONFLICTS" and decision.target_memory_id:
        store.add_relation(
            memory_id=memory_id,
            related_memory_id=decision.target_memory_id,
            relation_type="CONTRADICTS",
            note=decision.reason,
        )
        store.log_event(
            memory_id=memory_id,
            transcript_id=transcript_id,
            event="CONFLICT_RECORDED",
            reason=decision.reason,
            detail={"conflicts_with": decision.target_memory_id},
        )
        return MemoryOutcome(
            action="CONFLICT",
            memory_id=memory_id,
            content=candidate.content,
            memory_type=candidate.type,
            confidence=candidate.confidence,
            reason=decision.reason,
            target_memory_id=decision.target_memory_id,
        )

    return MemoryOutcome(
        action="CREATED",
        memory_id=memory_id,
        content=candidate.content,
        memory_type=candidate.type,
        confidence=candidate.confidence,
        reason=decision.reason or "new information",
    )


def process_pending(
    *,
    user_id: str,
    limit: int | None = None,
    engine: ReasoningEngine | None = None,
    progress: Callable[[int, int, ProcessResult], None] | None = None,
    workers: int = 1,
) -> list[ProcessResult]:
    """Process every transcript that has not been through extraction yet.

    Transcripts are *stored* oldest first, always. Corrections only make sense
    when the thing being corrected was learned earlier, so the reconcile-and-
    write half of the pipeline is strictly sequential and in timestamp order.

    `workers` only parallelises the half that is order-independent: asking the
    model what a dictation contains. Reading transcript #300 does not depend on
    what was learned from #299 - only on *storing* it does. With a remote model
    that call is almost entirely network wait, and running a few concurrently
    turns a twenty-minute pass over 500 records into a few minutes without
    changing a single stored outcome.
    """
    engine = engine or get_engine()
    pending = store.unprocessed_transcripts(user_id=user_id, limit=limit)
    total = len(pending)
    results: list[ProcessResult] = []

    if workers <= 1 or total <= 1:
        for index, transcript in enumerate(pending, start=1):
            result = process_transcript(transcript, user_id=user_id, engine=engine)
            results.append(result)
            if progress:
                progress(index, total, result)
        return results

    from concurrent.futures import ThreadPoolExecutor

    def extract_one(transcript: dict[str, Any]) -> ExtractionResult:
        return engine.extract(
            formatted_text=transcript.get("formatted_text") or "",
            raw_asr=transcript.get("raw_asr") or "",
            timestamp=transcript.get("timestamp") or "",
            application=transcript.get("application"),
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        # Submitted in order, consumed in order: the pool overlaps the waiting,
        # the loop below still sees transcripts oldest-first.
        futures = [pool.submit(extract_one, t) for t in pending]
        for index, (transcript, future) in enumerate(zip(pending, futures), start=1):
            result = process_transcript(
                transcript, user_id=user_id, engine=engine, extraction=future.result()
            )
            results.append(result)
            if progress:
                progress(index, total, result)

    return results
