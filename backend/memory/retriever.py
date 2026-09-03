"""Finding the few memories that matter for a question.

Retrieval is a blend of four signals rather than one similarity score, because
each one fails in a different place:

  semantic    cosine similarity between the question and the memory vector -
              catches paraphrase, but drifts on short questions
  lexical     BM25 over memory text - catches exact names and rare words, which
              is exactly where embeddings are weakest
  recency     newer memory wins ties, and wins harder when the question is
              about time ("when is my meeting" should not surface March)
  structure   entity match, memory type vs. question intent, extraction
              confidence, and memory status

Superseded memories are retrieved but heavily demoted: they never answer a
question, yet keeping them in the candidate set is what lets Kivi say
"it moved from 3 PM" and lets the Inspector show the correction.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.config import get_settings
from backend.llm.embeddings import cosine, get_embedder
from backend.memory import store
from backend.memory.query import QueryPlan, plan_query
from backend.memory.text import content_tokens, normalise

SUPERSEDED_PENALTY = 0.45  # multiplied into the final score
STATUS_ACTIVE = "ACTIVE"


@dataclass
class ScoredMemory:
    """A retrieval candidate with its score broken out, so it can be explained."""

    memory: dict[str, Any]
    score: float = 0.0
    semantic: float = 0.0
    lexical: float = 0.0
    recency: float = 0.0
    entity_bonus: float = 0.0
    type_bonus: float = 0.0
    coverage: float = 0.0

    def explain(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory["id"],
            "content": self.memory["content"],
            "type": self.memory["type"],
            "status": self.memory["status"],
            "score": round(self.score, 4),
            "semantic": round(self.semantic, 4),
            "lexical": round(self.lexical, 4),
            "recency": round(self.recency, 4),
            "entity_bonus": round(self.entity_bonus, 4),
            "type_bonus": round(self.type_bonus, 4),
            "coverage": round(self.coverage, 4),
            "source_transcript_id": self.memory.get("source_transcript_id"),
        }


@dataclass
class RetrievalResult:
    plan: QueryPlan
    scored: list[ScoredMemory] = field(default_factory=list)
    considered: int = 0
    latency_ms: float = 0.0

    @property
    def memories(self) -> list[dict[str, Any]]:
        return [s.memory for s in self.scored]

    @property
    def memory_ids(self) -> list[int]:
        return [s.memory["id"] for s in self.scored]

    def detail(self) -> list[dict[str, Any]]:
        return [s.explain() for s in self.scored]


# ---------------------------------------------------------------------------
# BM25
# ---------------------------------------------------------------------------
class BM25Index:
    """Classic BM25 over the memory corpus.

    Rebuilt per query. At the scale this product targets (a single user's
    dictation history, low thousands of memories) that costs a few
    milliseconds and removes a whole class of staleness bugs. A larger corpus
    would want this cached and invalidated on write - noted in the README.
    """

    K1 = 1.4
    B = 0.72

    def __init__(self, documents: list[list[str]]) -> None:
        self.documents = documents
        self.doc_count = len(documents) or 1
        self.lengths = [len(d) for d in documents]
        self.avg_length = (sum(self.lengths) / self.doc_count) or 1.0

        self.frequencies: list[dict[str, int]] = []
        document_frequency: dict[str, int] = {}
        for tokens in documents:
            counts: dict[str, int] = {}
            for token in tokens:
                counts[token] = counts.get(token, 0) + 1
            self.frequencies.append(counts)
            for token in counts:
                document_frequency[token] = document_frequency.get(token, 0) + 1

        self.idf = {
            term: math.log(1 + (self.doc_count - freq + 0.5) / (freq + 0.5))
            for term, freq in document_frequency.items()
        }
        self.max_idf = max(self.idf.values(), default=1.0)

    def score(self, index: int, query_tokens: list[str]) -> float:
        counts = self.frequencies[index]
        length = self.lengths[index] or 1
        total = 0.0
        for token in query_tokens:
            frequency = counts.get(token)
            if not frequency:
                continue
            idf = self.idf.get(token, 0.0)
            numerator = frequency * (self.K1 + 1)
            denominator = frequency + self.K1 * (1 - self.B + self.B * length / self.avg_length)
            total += idf * numerator / denominator
        return total


# ---------------------------------------------------------------------------
# Recency
# ---------------------------------------------------------------------------
def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def _recency_score(memory: dict[str, Any], newest: datetime | None) -> float:
    """1.0 for the most recent memory, decaying with a ~45 day half-life."""
    if newest is None:
        return 0.5
    moment = _parse_time(memory.get("occurred_at")) or _parse_time(
        memory.get("transcript_timestamp")
    ) or _parse_time(memory.get("created_at"))
    if moment is None:
        return 0.5
    age_days = max(0.0, (newest - moment).total_seconds() / 86400)
    return 0.5 ** (age_days / 45.0)


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
def retrieve(
    question: str,
    *,
    user_id: str,
    top_k: int | None = None,
    settings=None,
    include_superseded: bool = True,
) -> RetrievalResult:
    """Rank the user's memories against a question."""
    settings = settings or get_settings()
    top_k = top_k or settings.retrieval_top_k
    started = time.perf_counter()

    entities = store.known_entities(user_id)
    plan = plan_query(question, entities)

    corpus = store.load_retrievable(user_id)
    if not include_superseded:
        corpus = [m for m in corpus if m["status"] == STATUS_ACTIVE]

    if not corpus:
        return RetrievalResult(
            plan=plan, scored=[], considered=0, latency_ms=(time.perf_counter() - started) * 1000
        )

    # --- lexical index ----------------------------------------------------
    documents = [
        content_tokens(
            " ".join(
                [
                    memory.get("content") or "",
                    memory.get("subject") or "",
                    " ".join(memory.get("entities") or []),
                    " ".join(memory.get("tags") or []),
                    (memory.get("attribute") or "").replace("_", " "),
                    memory.get("value") or "",
                ]
            )
        )
        for memory in corpus
    ]
    index = BM25Index(documents)

    # The user's own words and the terms the intent implies are scored
    # separately. Mixed into one bag they compete as equals, and the intent
    # expansions win: "who is the finance lead" expands with role vocabulary
    # ("works", "team", "role"), which then matches every team-membership
    # sentence better than it matches the one sentence containing "finance".
    # Expansions are meant to widen the net, not to outvote the question.
    question_tokens = content_tokens(" ".join([plan.question] + plan.entities))
    expansion_tokens = content_tokens(" ".join(plan.expansion_terms))
    EXPANSION_WEIGHT = 0.3

    # --- semantic ---------------------------------------------------------
    embedder = get_embedder()
    query_vector = embedder.embed(plan.search_text)

    # --- reference point for recency -------------------------------------
    stamps = [
        t
        for t in (
            _parse_time(m.get("occurred_at"))
            or _parse_time(m.get("transcript_timestamp"))
            or _parse_time(m.get("created_at"))
            for m in corpus
        )
        if t is not None
    ]
    newest = max(stamps) if stamps else None

    entity_keys = [normalise(e) for e in plan.entities]
    type_boosts = plan.type_boosts()
    attribute_boosts = plan.attribute_boosts()
    # Ranking uses the discriminative set, not the abstention set - see QueryPlan.
    residual_stems = [t[:5] for t in plan.discriminative_tokens]
    recency_weight = settings.recency_weight * (1.8 if plan.time_sensitive else 1.0)

    scored: list[ScoredMemory] = []
    raw_question = [index.score(p, question_tokens) for p in range(len(corpus))]
    raw_expansion = (
        [index.score(p, expansion_tokens) for p in range(len(corpus))]
        if expansion_tokens
        else [0.0] * len(corpus)
    )
    max_question = max(raw_question, default=0.0) or 1.0
    max_expansion = max(raw_expansion, default=0.0) or 1.0

    for position, memory in enumerate(corpus):
        semantic = cosine(query_vector, memory.get("vector") or [])
        lexical = (
            raw_question[position] / max_question
            + EXPANSION_WEIGHT * (raw_expansion[position] / max_expansion)
        ) / (1 + EXPANSION_WEIGHT)
        recency = _recency_score(memory, newest)

        # Naming a person in the question is a strong, explicit signal - much
        # stronger than a similarity metric will ever infer on its own.
        haystack = normalise(
            " ".join(
                [
                    memory.get("content") or "",
                    memory.get("subject") or "",
                    " ".join(memory.get("entities") or []),
                ]
            )
        )
        entity_bonus = 0.0
        if entity_keys:
            hits = sum(1 for key in entity_keys if key and key in haystack)
            if hits:
                entity_bonus = min(0.40, 0.28 + 0.08 * (hits - 1))

        type_bonus = type_boosts.get(memory.get("type") or "", 0.0)
        type_bonus += attribute_boosts.get((memory.get("attribute") or "").lower(), 0.0)

        # The words in the question are what separates "the revised numbers"
        # from "the numbers still do not line up", and "the engineering manager
        # on Cobalt" from "sits on the Data team and works on Cobalt". A memory
        # covering more of them is more likely to be the one being asked about.
        coverage = 0.0
        if residual_stems:
            memory_words = set(documents[position])
            covered = sum(
                1
                for stem in residual_stems
                if any(word.startswith(stem) for word in memory_words)
            )
            coverage = 0.28 * (covered / len(residual_stems))

        score = (
            settings.semantic_weight * semantic
            + settings.lexical_weight * lexical
            + recency_weight * recency
            + entity_bonus
            + type_bonus
            + coverage
        )
        # A memory Kivi was unsure about should not outrank one it was sure of.
        score *= 0.75 + 0.25 * float(memory.get("confidence") or 0.5)

        if memory["status"] != STATUS_ACTIVE:
            score *= SUPERSEDED_PENALTY

        scored.append(
            ScoredMemory(
                memory=memory,
                score=score,
                semantic=semantic,
                lexical=lexical,
                recency=recency,
                entity_bonus=entity_bonus,
                type_bonus=type_bonus,
                coverage=coverage,
            )
        )

    scored.sort(key=lambda s: s.score, reverse=True)

    # An entity named in the question acts as a filter, not just a bonus: if
    # Kivi knows about "Rahul", a memory that never mentions Rahul is not an
    # answer to a question about Rahul, however similar it looks.
    #
    # Preferences are the exception. "Keep my client emails short" is about the
    # user, not about Rahul, but it is exactly what should shape a draft to
    # Rahul - so preference memories survive the filter.
    if entity_keys:
        def mentions(item: ScoredMemory) -> bool:
            haystack = normalise(
                " ".join(
                    [
                        item.memory.get("content") or "",
                        item.memory.get("subject") or "",
                        " ".join(item.memory.get("entities") or []),
                    ]
                )
            )
            return any(key in haystack for key in entity_keys if key)

        on_topic = [s for s in scored if mentions(s)]
        if on_topic:
            keep_preferences = [
                s for s in scored if s.memory.get("type") == "preference" and not mentions(s)
            ][:2]
            scored = sorted(on_topic + keep_preferences, key=lambda s: s.score, reverse=True)

    kept = [
        s for s in scored[: settings.retrieval_candidates] if s.score >= settings.min_retrieval_score
    ]
    kept = kept[:top_k]

    # Reserve a slot for how the user likes things written. A draft addressed to
    # Rahul is dominated by memories about Rahul, and "keep my client emails
    # short" - the one memory that should shape the draft - never scores high
    # enough to survive the cut on its own.
    from backend.memory.query import INTENT_DRAFT, INTENT_PREFERENCE

    if plan.intent in (INTENT_DRAFT, INTENT_PREFERENCE) and kept:
        if not any(s.memory.get("type") == "preference" for s in kept):
            preferences = [
                s
                for s in scored
                if s.memory.get("type") == "preference" and s.memory["status"] == STATUS_ACTIVE
            ][:2]
            if preferences:
                kept = (kept[: max(1, top_k - len(preferences))] + preferences)
                kept.sort(key=lambda s: s.score, reverse=True)

    return RetrievalResult(
        plan=plan,
        scored=kept,
        considered=len(corpus),
        latency_ms=(time.perf_counter() - started) * 1000,
    )


def to_answer_context(result: RetrievalResult) -> list[dict[str, Any]]:
    """Shape retrieved memories for the answering prompt.

    Includes a short excerpt of the source transcript, so the model can ground
    a claim in the user's actual words rather than only in Kivi's paraphrase.
    """
    # Reconciliation already decided which of these disagree with each other.
    # Passing that through is what stops an answer listing two contradictory
    # times as if they were two different appointments.
    contradictions = store.contradictions_among([s.memory["id"] for s in result.scored])

    context: list[dict[str, Any]] = []
    for item in result.scored:
        memory = item.memory
        source_text = memory.get("source_text") or ""
        excerpt = source_text if len(source_text) <= 220 else source_text[:217] + "..."
        context.append(
            {
                "id": memory["id"],
                "type": memory["type"],
                "status": memory["status"],
                "content": memory["content"],
                "subject": memory.get("subject"),
                "attribute": memory.get("attribute"),
                "value": memory.get("value"),
                "entities": memory.get("entities") or [],
                "tags": memory.get("tags") or [],
                "confidence": memory.get("confidence"),
                "timestamp": memory.get("transcript_timestamp") or memory.get("created_at"),
                "occurred_at": memory.get("occurred_at"),
                "application": memory.get("application"),
                "source_transcript_id": memory.get("source_transcript_id"),
                "source_excerpt": excerpt,
                "score": round(item.score, 4),
                "contradicts": contradictions.get(memory["id"], []),
            }
        )
    return context


# ---------------------------------------------------------------------------
# The fallback: searching what was said, when nothing was learned
# ---------------------------------------------------------------------------
# Memories answer; transcripts only rescue. This exists because the memory
# index has one structural weakness: a dictation that produced no memory - the
# extractor ignored it, or rejected it below the confidence threshold - is
# stored but unreachable, and a wrong judgement there erases the content
# entirely rather than merely degrading the answer.
#
# It is deliberately NOT a second index consulted on every question. Transcript
# #3 still says "Rahul is Monday at 10 AM" long after memory #3 was superseded;
# searching both stores every time would put that dead time back in front of the
# model and undo reconciliation. So the fallback fires only when the memory
# search comes back with nothing worth answering from.
def retrieve_transcripts(
    question: str,
    *,
    user_id: str,
    limit: int = 4,
    settings=None,
) -> list[dict[str, Any]]:
    """Rank raw dictations against a question. Used only as a fallback."""
    settings = settings or get_settings()
    plan = plan_query(question, store.known_entities(user_id))
    terms = plan.tokens
    if not terms:
        return []

    records = store.load_searchable_transcripts(user_id=user_id)
    if not records:
        return []

    # Both halves of the record are indexed: the formatted text is what a person
    # would read, but the recogniser output sometimes keeps a word the formatter
    # smoothed away.
    documents = [
        content_tokens(f"{r.get('formatted_text') or ''} {r.get('raw_asr') or ''}")
        for r in records
    ]
    index = BM25Index(documents)

    ranked = sorted(
        (
            (index.score(i, terms), record)
            for i, record in enumerate(records)
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    ranked = [(score, record) for score, record in ranked if score > 0][:limit]

    return [
        {
            # Negative, so a transcript can never be mistaken for a memory id in
            # `used_memory_ids` or in a citation. The sign is the type tag.
            "id": -int(record["id"]),
            "transcript_id": int(record["id"]),
            # the citation machinery reads this name
            "source_transcript_id": int(record["id"]),
            "source_excerpt": record.get("formatted_text") or "",
            "confidence": None,
            "from_transcript": True,
            "type": "dictation",
            "status": "UNLEARNED",
            "content": record.get("formatted_text") or "",
            "timestamp": record.get("timestamp"),
            "application": record.get("application"),
            "score": round(float(score), 4),
        }
        for score, record in ranked
    ]
