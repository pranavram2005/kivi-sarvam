"""Understanding the question before searching for an answer.

Retrieval quality depends far more on reading the question correctly than on
the similarity metric. This module turns a Hey Kivi question into:

  * an intent   - what shape of answer is being asked for
  * entities    - the people and projects named
  * residuals   - the topic words that must be supported by memory, which is
                  what lets Kivi tell "I have nothing about Rahul's birthday"
                  apart from "I have plenty about Rahul"
  * expansions  - extra retrieval terms implied by the intent, so "what should
                  I prepare" also searches for commitments and deadlines
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from backend.memory.text import (
    PREFERENCE_WORDS,
    STOPWORDS,
    TASK_WORDS,
    TIME_WORDS,
    content_tokens,
    normalise,
)

# Intents Kivi recognises. Each one changes which memory types get boosted and
# which words are treated as "asking words" rather than "topic words".
INTENT_WHEN = "when"
INTENT_WHO = "who"
INTENT_PREPARE = "prepare"
INTENT_DISCUSSED = "discussed"
INTENT_PREFERENCE = "preference"
INTENT_DRAFT = "draft"
INTENT_WHY = "why"
INTENT_GENERAL = "general"

_INTENT_CUES: dict[str, tuple[str, ...]] = {
    INTENT_DRAFT: ("draft", "write", "compose", "reply", "email to", "message to", "send a note"),
    INTENT_WHY: ("why do you", "why did you", "why think", "how do you know", "what makes you"),
    INTENT_PREPARE: (
        "prepare",
        "prep for",
        "get ready",
        "need to do",
        "should i send",
        "what do i owe",
        "before the meeting",
        "ahead of",
        "follow up",
        "action items",
        "outstanding",
    ),
    INTENT_WHEN: ("when is", "when's", "what time", "what day", "when do i", "when are"),
    INTENT_WHO: (
        "who is",
        "who's",
        "who leads",
        "who owns",
        "who runs",
        "who handles",
        "who works",
        "who manages",
        "who heads",
        "who reviews",
        "who should i",
        "who do i",
    ),
    INTENT_PREFERENCE: (
        "how do i like",
        "what's my style",
        "my usual",
        "do i prefer",
        "what do i prefer",
        "how should",
    ),
    INTENT_DISCUSSED: (
        "what did i say",
        "what was i discussing",
        "what did we discuss",
        "what did i discuss",
        "how much longer",
        "talked about",
        "what's happening with",
        "whats happening with",
        "status of",
        "latest on",
        "update on",
    ),
}

# Words that belong to the *asking*, not to the *topic*. They must never be
# treated as evidence requirements, or every question would abstain.
#
# The distinction this list draws is the one that makes abstention work:
# "what was I *discussing* with Sarah" is a question shape, so Kivi answers from
# whatever it holds about Sarah; "when is Rahul's *birthday*" names a topic Kivi
# has no memory of, so it says so. Verbs of conversation and work belong here;
# nouns that name a subject do not.
_ASKING_WORDS = frozenset(
    """
    tell know knows remember recall say said says saying speak spoke
    discuss discussing discussed discussion talk talking talked
    mention mentioned mentioning raise raised bring brought cover covered
    thing things anything something everything stuff
    about regarding concerning around
    need needs needed want wants wanted going get gets give gives show shows
    happening happened happens latest update updates updated
    lead leads leading head heads run runs own owns manage manages
    work works working
    do does did done make makes made take takes took
    ask asks asked answer answers
    think thinks thought believe believes reckon suppose
    find finds found figure figured see saw seen look looked
    much many more most less least long longer longest far
    else new next other others first last best worse better
    """.split()
)

# Words that mean nothing in any question - they cannot help rank a memory, so
# ranking drops them. Much smaller than _ASKING_WORDS on purpose: a verb like
# "manages" is useless as an evidence requirement but very useful for ranking.
_PURE_QUESTION_WORDS = frozenset(
    """
    tell know knows remember recall thing things anything something everything
    stuff about regarding concerning around need needs want wants going
    else much many more most please just really actually
    """.split()
)

# Retrieval terms added for each intent, so the search covers the concepts the
# question implies rather than only the words it contains.
_INTENT_EXPANSIONS: dict[str, tuple[str, ...]] = {
    INTENT_PREPARE: ("send", "before", "deadline", "meeting", "revised", "task", "owe", "bring"),
    INTENT_WHEN: ("meeting", "scheduled", "moved", "time", "day"),
    INTENT_WHO: ("leads", "owns", "role", "works", "team", "lead"),
    INTENT_DISCUSSED: ("discussed", "mentioned", "raised", "talked", "topic"),
    INTENT_PREFERENCE: ("prefers", "style", "tone", "keep", "usual"),
    INTENT_DRAFT: ("prefers", "style", "tone", "concise", "meeting", "send"),
    INTENT_WHY: ("because", "said", "mentioned"),
}

# Which memory types matter most for each intent.
INTENT_TYPE_BOOSTS: dict[str, dict[str, float]] = {
    INTENT_WHEN: {"event": 0.28, "episode": 0.05},
    INTENT_WHO: {"fact": 0.25},
    INTENT_PREPARE: {"task": 0.24, "event": 0.16, "episode": 0.08},
    INTENT_DISCUSSED: {"episode": 0.20, "task": 0.06},
    INTENT_PREFERENCE: {"preference": 0.34},
    INTENT_DRAFT: {"preference": 0.26, "event": 0.10, "task": 0.10},
    INTENT_WHY: {"episode": 0.12, "task": 0.12},
    INTENT_GENERAL: {},
}


# Some questions are answered by an attribute rather than a memory type. "When
# is the deadline" is a `when` question, but a deadline is rarely phrased as a
# meeting, so boosting only the `event` type would bury it.
INTENT_ATTRIBUTE_BOOSTS: dict[str, dict[str, float]] = {
    INTENT_WHEN: {"meeting_time": 0.24, "deadline": 0.24, "meeting_location": 0.10},
    INTENT_WHO: {"role": 0.22, "project": 0.10},
    INTENT_PREFERENCE: {"email_style": 0.22, "summary_style": 0.22, "tone": 0.22, "style": 0.18},
    INTENT_DRAFT: {"email_style": 0.20, "tone": 0.18, "style": 0.16},
    INTENT_PREPARE: {"deliverable": 0.20, "deadline": 0.12},
}


@dataclass
class QueryPlan:
    """The parsed form of a Hey Kivi question."""

    question: str
    intent: str = INTENT_GENERAL
    tokens: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)

    # Two different jobs, deliberately kept apart.
    #
    # `residual_tokens` decides ABSTENTION: topic words that must appear
    # somewhere in memory, or Kivi has nothing to say. Role verbs are excluded,
    # because Kivi may hold the fact in different words ("manages" vs "manager")
    # and refusing to answer over vocabulary would be wrong.
    #
    # `discriminative_tokens` decides RANKING: the words that separate the right
    # memory from the merely related ones. "Who manages Project Cobalt" and "who
    # is on Project Cobalt" have the same entity and the same residuals; only
    # the verb tells them apart, so ranking keeps it.
    residual_tokens: list[str] = field(default_factory=list)
    discriminative_tokens: list[str] = field(default_factory=list)

    expansion_terms: list[str] = field(default_factory=list)
    time_sensitive: bool = False

    @property
    def search_text(self) -> str:
        """The text actually embedded and matched against memory."""
        parts = [self.question] + self.entities + self.expansion_terms
        return " ".join(parts)

    def type_boosts(self) -> dict[str, float]:
        return INTENT_TYPE_BOOSTS.get(self.intent, {})

    def attribute_boosts(self) -> dict[str, float]:
        return INTENT_ATTRIBUTE_BOOSTS.get(self.intent, {})

    def as_dict(self) -> dict[str, object]:
        return {
            "intent": self.intent,
            "entities": self.entities,
            "residual_tokens": self.residual_tokens,
            "expansion_terms": self.expansion_terms,
            "time_sensitive": self.time_sensitive,
        }


def _bare(text: str) -> str:
    """Normalised text with punctuation replaced by spaces.

    Question marks matter here: without stripping them, " project cobalt " never
    matches inside "who manages project cobalt?", and the whole entity-aware
    half of retrieval silently stops working on the last word of a question.
    """
    return " " + re.sub(r"[^a-z0-9]+", " ", normalise(text)).strip() + " "


def detect_intent(question: str) -> str:
    lowered = _bare(question)
    for intent, cues in _INTENT_CUES.items():
        if any(f" {cue} " in lowered or lowered.startswith(f" {cue} ") for cue in cues):
            return intent

    # Fall back to the leading question word. This reads the raw question, not
    # the content tokens - "who", "when" and "why" are all stopwords, so they
    # are gone by the time tokenisation finishes.
    first = lowered.strip().split(" ")[0] if lowered.strip() else ""
    if first == "when":
        return INTENT_WHEN
    if first == "who":
        return INTENT_WHO
    if first == "why":
        return INTENT_WHY
    return INTENT_GENERAL


def extract_entities(question: str, known_entities: Iterable[str] = ()) -> list[str]:
    """Names mentioned in the question.

    Two passes: anything Kivi already knows about (matched case-insensitively
    on the whole phrase, so "Project Atlas" survives), then capitalised words
    that survived normalisation as a fallback for names Kivi has not met.
    """
    lowered = _bare(question)
    found: list[str] = []
    seen: set[str] = set()

    for entity in sorted(known_entities, key=len, reverse=True):
        key = normalise(entity)
        needle = f" {re.sub(r'[^a-z0-9]+', ' ', key).strip()} "
        if needle in lowered and key not in seen:
            seen.add(key)
            found.append(entity)

    if not found:
        words = question.replace("?", " ").replace(",", " ").split()
        for index, word in enumerate(words):
            bare = word.strip("'’s.:;\"")
            if not bare or not bare[0].isupper():
                continue
            if index == 0 and bare.lower() in STOPWORDS | _ASKING_WORDS:
                continue
            if bare.lower() in STOPWORDS or bare.lower() in _ASKING_WORDS:
                continue
            key = bare.lower()
            if key not in seen:
                seen.add(key)
                found.append(bare)
    return found


def plan_query(question: str, known_entities: Iterable[str] = ()) -> QueryPlan:
    """Parse a question into everything retrieval and answering need."""
    intent = detect_intent(question)
    entities = extract_entities(question, known_entities)
    tokens = content_tokens(question)

    entity_tokens: set[str] = set()
    for entity in entities:
        entity_tokens.update(content_tokens(entity))

    # Residual tokens are the ones that carry a topic requirement: not the
    # entity, not the question word, not the intent's own vocabulary. If none of
    # these can be supported by memory, Kivi has nothing to say.
    ignorable = (
        entity_tokens
        | _ASKING_WORDS
        | TIME_WORDS
        | TASK_WORDS
        | PREFERENCE_WORDS
        | {"meeting", "meetings", "call", "email", "message", "note", "draft"}
    )
    residual = [t for t in tokens if t not in ignorable and len(t) > 2]

    # Ranking keeps everything the topic filter threw away except the entity
    # itself and words that carry no meaning in any question.
    discriminative = [
        t
        for t in tokens
        if t not in entity_tokens and t not in _PURE_QUESTION_WORDS and len(t) > 2
    ]

    plan = QueryPlan(
        question=question,
        intent=intent,
        tokens=tokens,
        entities=entities,
        residual_tokens=residual,
        discriminative_tokens=discriminative,
        expansion_terms=list(_INTENT_EXPANSIONS.get(intent, ())),
        time_sensitive=bool(set(tokens) & TIME_WORDS) or intent == INTENT_WHEN,
    )
    return plan
