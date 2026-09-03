"""The offline reasoning engine.

This is a deterministic, rule-driven implementation of the same three decisions
an LLM makes: what to remember, how a new memory relates to old ones, and what
can honestly be said in answer to a question.

Why it exists
-------------
A reviewer must be able to clone this repository, run one command, and see the
entire pipeline work — extraction, correction, retrieval, provenance,
abstention, evaluation — without an API key, a network connection, or a bill.
Setting `KIVI_LLM_PROVIDER=anthropic` (or openai / gemini) swaps this engine for
a real model through the identical interface; nothing else in the system changes.

What it is not
--------------
It is not a language model, and it does not pretend to be. It reads cue words
and sentence shapes rather than meaning, so it will miss memories phrased in a
way it has no rule for. Its one real advantage — beyond running anywhere — is
that it is incapable of inventing a fact, because every sentence it produces is
assembled from stored memory text. The evaluation reports both engines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from backend.llm.engine import (
    AnswerResult,
    ExtractedMemory,
    ExtractionResult,
    ReasoningEngine,
    ResolutionDecision,
)
from backend.llm.base import LLMUsage
from backend.memory.query import (
    INTENT_DISCUSSED,
    INTENT_DRAFT,
    INTENT_PREFERENCE,
    INTENT_PREPARE,
    INTENT_WHEN,
    INTENT_WHO,
    INTENT_WHY,
)
from backend.memory.text import content_tokens, normalise

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

# Dictations that are pure thinking-aloud. Matched against the whole utterance.
_FILLER_PATTERNS = [
    r"^(hmm+|umm+|uh+|erm+|ah+|oh+)\b",
    r"\b(give me a (second|sec|minute|moment))\b",
    r"^(ok(ay)?|right|alright|cool|sure|yeah|yep|nope|no)[\s,.!]*$",
    r"\b(let me (think|see|check that)|hold on|one sec|never ?mind|scratch that,? nothing)\b",
    r"\b(testing|test) (one|1)[\s,]*(two|2)\b",
    r"^(what was i saying|where was i|sorry,? what)\b",
    r"\b(can you hear me|is this (thing )?on)\b",
    r"^(delete that|ignore that|forget (that|it))[\s,.!]*$",
]
_FILLER_RE = re.compile("|".join(_FILLER_PATTERNS), re.IGNORECASE)

# Sentences that are being *dictated as content* rather than stated as fact.
_DICTATION_MARKERS = re.compile(
    r"^(dear |hi |hello |hey )[a-z]+[,\s]|^(kind regards|best regards|thanks,|cheers,)",
    re.IGNORECASE,
)

_CORRECTION_CUES = re.compile(
    r"\b(actually|correction|scratch that|instead of|instead|rather than|"
    # "push the Aditi Cobalt schema check-in to Thursday" - the verb and the
    # "to" are separated by however much of the thing's name the user said, so
    # the gap has to be allowed rather than enumerated.
    r"(?:move[ds]?|moving|push(?:ed|ing)?|shift(?:ed|ing)?|bump(?:ed|ing)?|"
    r"reschedul\w*|slide|slid)\b[^.?!]{0,70}?\bto\b|"
    r"changed? (?:it |that |the )?to|"
    r"no longer|not any ?more|update:|correction:|i meant|make that|let'?s make it|"
    r"switch(?:ed)? (?:it |that )?to)\b",
    re.IGNORECASE,
)

# How the user wants their own writing to read. The adjective list is
# deliberately wide: a preference is recognised by its *shape* ("keep X <adj>"),
# and restricting the adjective to a handful of length words meant a sentence
# like "Keep my release notes plain" fell through to a generic episode and was
# then rejected for low confidence - a stated preference, silently lost.
_STYLE_ADJECTIVES = (
    r"short|brief|concise|tight|long|detailed|punchy|formal|informal|casual|"
    r"plain|simple|clear|direct|blunt|crisp|factual|neutral|warm|friendly|"
    r"polite|professional|technical|readable|light|serious|specific"
)

_PREFERENCE_CUES = re.compile(
    r"\b(prefer\w*|i like (?:my|to)|always (?:use|keep|send|write)|never (?:use|send|write)|"
    rf"(?:keep|make|write|leave)\s+(?:\w+\s+){{0,4}}?(?:{_STYLE_ADJECTIVES})\b|"
    r"my usual|as usual|should (?:always )?(?:be|use|stay)|make sure (?:they|it|these) (?:are|is)|"
    r"in bullet ?points?|bullet ?points?|one ?pager|writing style|my style|my tone|"
    r"(?:keep|hold)\s+(?:\w+\s+){0,4}?to\s+\w+\s+(?:sentences?|words?|lines?|bullets?|pages?)|"
    # "with no marketing language", "without any jargon" - a rule about wording
    r"(?:with )?(?:no|without)\s+(?:\w+\s+){0,2}?(?:language|jargon|wording|waffle|fluff)|"
    r"(?:no|not) longer than|at most|never (?:run|go) (?:longer|over)|"
    r"open with the|lead with the)\b",
    re.IGNORECASE,
)

# Something the user did with someone - the verb that makes a short sentence an
# episode rather than noise.
_DISCUSSION_RE = re.compile(
    r"\b(discussed?|discussing|talked|spoke|mentioned|raised|asked|agreed|"
    r"walked through|went over|caught up|reviewed|covered|flagged|brought up)\b",
    re.IGNORECASE,
)

_EVENT_NOUNS = (
    "meeting",
    "call",
    "sync",
    "standup",
    "stand-up",
    "review",
    "1:1",
    "one on one",
    "catch up",
    "catch-up",
    "demo",
    "interview",
    "workshop",
    "retro",
    "session",
    "presentation",
    "kickoff",
    "kick-off",
    "check-in",
    "checkin",
    "offsite",
    "walkthrough",
    "walk-through",
    "run-through",
    "briefing",
    "huddle",
    "deep dive",
    "debrief",
)
_EVENT_RE = re.compile(r"\b(" + "|".join(re.escape(n) for n in _EVENT_NOUNS) + r")\b", re.IGNORECASE)

# An explicit, first-person promise. Stronger than the general task cues, and
# checked before anything else that could mistake it for a scheduled event.
_COMMITMENT_RE = re.compile(
    r"\b(i promised|i owe|i said i(?:'d| would)|i agreed to|i committed to|"
    r"i need to (?:send|share|review|prepare|write|get|finish|update|book)|"
    r"i(?:'ll| will) (?:send|share|review|prepare|write|get|finish|update|book)|"
    r"i have to (?:send|share|review|prepare|write|get|finish|update|book))\b",
    re.IGNORECASE,
)

_TASK_CUES = re.compile(
    r"\b(need to|needs to|have to|has to|must|i'?ll|i will|going to|gonna|remember to|"
    r"don'?t forget to|make sure to|should (?:send|share|prepare|write|book|check|call|email|"
    r"finish|review|update)|owe[sd]? \w+|action item|to ?do|follow ?up|circle back|"
    r"send|share|prepare|deliver|submit|book|draft)\b",
    re.IGNORECASE,
)

_FACT_RE = re.compile(
    r"\b(is|are|was|were)\s+(?:the|our|my|a|an)?\s*\w+|"
    r"\b(leads?|heads?|runs?|owns?|manages?|reports? to|works? (?:on|with|at)|"
    r"joined|handles?|drives?|sits (?:on|in))\b",
    re.IGNORECASE,
)

_DAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

_TIME_PHRASE_RE = re.compile(
    r"\b("
    r"(?:next |this |last )?(?:" + "|".join(_DAY_NAMES) + r")"
    r"(?:\s+(?:at|@)\s*\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?)?"
    r"|tomorrow(?:\s+(?:at|@)\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?)?"
    r"|today(?:\s+(?:at|@)\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?)?"
    r"|yesterday"
    r"|tonight"
    r"|(?:next|this|last) (?:week|month|monday|quarter)"
    r"|end of (?:the )?(?:day|week|month|quarter|august|september|october|november|december|"
    r"january|february|march|april|may|june|july)"
    r"|(?:january|february|march|april|may|june|july|august|september|october|november|december)"
    r"(?:\s+\d{1,2}(?:st|nd|rd|th)?)?"
    r"|the\s+\d{1,2}(?:st|nd|rd|th)"
    r"|\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)"
    r"|\d{1,2}(?::\d{2})\b"
    r")",
    re.IGNORECASE,
)

# Slots that hold exactly one current value. A second value for the same slot is
# either a correction or a genuine contradiction - never simply "more memory".
SINGLE_VALUED_ATTRIBUTES = frozenset(
    {
        "meeting_time",
        "meeting_location",
        "deadline",
        "role",
        "status",
        "priority",
        "budget",
        "email_style",
        "summary_style",
        "tone",
        "contact",
    }
)

# Slots where a later dictation with a different value is simply the newer
# truth: a deadline or a status has exactly one current value per subject, and
# stating a new one replaces the old one.
VOLATILE_ATTRIBUTES = frozenset({"deadline", "status", "priority", "budget"})

# Slots where a second value is ambiguous on its own. A person has many
# meetings, so "Meeting with Rahul on Tuesday" does not replace "Meeting with
# Rahul on Friday" - unless the two are about the same thing, in which case
# either it is a correction (an explicit cue) or a genuine conflict.
RESCHEDULABLE_ATTRIBUTES = frozenset({"meeting_time", "meeting_location"})

# Attributes whose value is owned by one person. Two people holding different
# roles on the same project is not a contradiction.
PERSON_SCOPED_ATTRIBUTES = frozenset({"role", "contact"})

# Words that describe *when*, stripped out before comparing what two event
# memories are actually about.
_TIME_TOKENS = frozenset(
    """
    monday tuesday wednesday thursday friday saturday sunday
    today tomorrow yesterday tonight morning afternoon evening
    am pm oclock next this last week month quarter end
    """.split()
)


def _topic_tokens(text: str) -> set[str]:
    """The subject matter of a sentence, with times and digits removed."""
    return {t for t in content_tokens(text) if t not in _TIME_TOKENS and not t.isdigit()}


def _project_entities(record: dict[str, Any]) -> set[str]:
    """Project/workstream keys on a memory, normalised for correction scoping.

    The corpus uses both full names ("Project Delta") and short names
    ("Forge"). For a person-scoped meeting, the subject is usually the person,
    so every other entity is treated as the appointment's topic key.
    """
    subject = normalise(record.get("subject") or "")
    values = [record.get("subject") or ""]
    values.extend(record.get("entities") or [])
    keys: set[str] = set()
    project_prefixes = ("project ", "initiative ", "program ", "workstream ")
    for value in values:
        if not isinstance(value, str):
            continue
        key = normalise(value)
        if not key or key == "user":
            continue
        if key == subject and not key.startswith(project_prefixes):
            continue
        for prefix in project_prefixes:
            if key.startswith(prefix):
                key = key[len(prefix) :]
                break
        if key:
            keys.add(key)
    return {
        key
        for key in keys
        if key not in {"meeting", "call", "sync", "review", "standup", "check", "check in"}
    }


def _same_topic(a: str, b: str, threshold: float = 0.5) -> bool:
    """Do two event memories describe the same appointment?"""
    left, right = _topic_tokens(a), _topic_tokens(b)
    if not left or not right:
        return False
    overlap = len(left & right) / len(left | right)
    return overlap >= threshold

_ATTRIBUTE_BY_CUE: list[tuple[re.Pattern[str], str]] = [
    # Order matters: the first pattern that matches wins. Identity comes first,
    # because "Sarah is the finance lead and signs off on any pricing change"
    # is a statement about Sarah's role that merely mentions pricing - filing it
    # under `budget` loses the fact the sentence exists to record.
    (
        re.compile(
            r"\b(is|are)\s+(?:the|our|my)\s+[\w\s]{0,24}?"
            r"(lead|leads?|head|manager|director|counsel|owner|engineer|designer|"
            r"scientist|marketer|recruiter|executive|analyst|architect)\b",
            re.I,
        ),
        "role",
    ),
    (
        re.compile(
            r"\b(leads?|heads?|runs?|owns?|manages?|counsel|reviews|approves|"
            r"signs? off|responsible for|point of contact|handles)\b",
            re.I,
        ),
        "role",
    ),
    (re.compile(r"\b(works? on|working on|assigned to|part of|sits (?:on|in))\b", re.I), "project"),
    (
        re.compile(
            r"\b(email|emails|mail)\b.*\b(short|brief|concise|long|detailed|tone)\b", re.I
        ),
        "email_style",
    ),
    (re.compile(r"\b(summary|summaries|notes?|recap)\b", re.I), "summary_style"),
    (re.compile(r"\b(tone|voice|formal|informal|professional|casual)\b", re.I), "tone"),
    (
        re.compile(
            r"\b(deadline|due|by (?:end of|eod|friday|monday)|cut ?off|"
            r"ships?|shipping|shipped|launch(?:es|ed|ing)?|release date|goes live)\b",
            re.I,
        ),
        "deadline",
    ),
    (re.compile(r"\b(budget|cost|pricing|price)\b", re.I), "budget"),
    (re.compile(r"\b(room|zoom|meet|teams|office|floor|call link)\b", re.I), "meeting_location"),
    (re.compile(r"\b(blocked|on track|at risk|slipping|done|shipped|paused)\b", re.I), "status"),
]

# Words that look capitalised but are not names.
_NON_NAMES = frozenset(
    """
    i i'm i'll monday tuesday wednesday thursday friday saturday sunday
    january february march april may june july august september october
    november december slack notes mail messages terminal cursor reminders
    the a an we they he she it this that there here ok okay yes no
    """.split()
)

_NAME_RE = re.compile(r"\b([A-Z][a-z]{1,20})\b")
_PROJECT_RE = re.compile(r"\b((?:Project|Initiative|Program|Workstream)\s+[A-Z][A-Za-z0-9-]+)\b")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [p.strip() for p in parts if p and p.strip()]


def _find_entities(text: str) -> list[str]:
    """Named people and projects mentioned in a sentence."""
    found: list[str] = []
    seen: set[str] = set()

    for match in _PROJECT_RE.finditer(text):
        name = match.group(1).strip()
        if name.lower() not in seen:
            seen.add(name.lower())
            found.append(name)

    masked = _PROJECT_RE.sub(" ", text)
    for index, match in enumerate(_NAME_RE.finditer(masked)):
        name = match.group(1)
        if name.lower() in _NON_NAMES:
            continue
        # A capitalised first word is usually just the start of the sentence.
        if match.start() == 0 and index == 0 and not _looks_like_name(name, masked):
            continue
        if name.lower() not in seen:
            seen.add(name.lower())
            found.append(name)
    return found


def _looks_like_name(word: str, text: str) -> bool:
    """A sentence-initial capital is a name if a verb of attribution follows."""
    tail = text[len(word) :].lstrip()
    return bool(
        re.match(
            r"^(is|was|leads?|heads?|runs?|owns?|manages?|works?|wants?|asked|said|needs?|"
            r"reports?|joined|will|has|had|prefers?|sent|mentioned)\b",
            tail,
            re.IGNORECASE,
        )
    )


def _time_phrase(text: str) -> str | None:
    matches = _TIME_PHRASE_RE.findall(text)
    if not matches:
        return None
    # Prefer the longest match - "Friday at 4 PM" beats "Friday".
    phrases = [m[0] if isinstance(m, tuple) else m for m in matches]
    return max(phrases, key=len).strip()


def _resolve_occurred_at(phrase: str | None, reference: str) -> str | None:
    """Turn 'Friday at 4 PM' into an ISO timestamp relative to the dictation."""
    if not phrase:
        return None
    try:
        base = datetime.fromisoformat(reference.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None

    lowered = phrase.lower()
    target = base
    matched_day = False

    if "tomorrow" in lowered:
        target = base + timedelta(days=1)
        matched_day = True
    elif "yesterday" in lowered:
        target = base - timedelta(days=1)
        matched_day = True
    elif "today" in lowered or "tonight" in lowered:
        matched_day = True
    else:
        for index, day in enumerate(_DAY_NAMES):
            if day in lowered:
                delta = (index - base.weekday()) % 7
                if delta == 0 and "next" in lowered:
                    delta = 7
                elif "next" in lowered:
                    delta += 7
                target = base + timedelta(days=delta)
                matched_day = True
                break

    hour_match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?", lowered)
    if hour_match:
        hour = int(hour_match.group(1))
        minute = int(hour_match.group(2) or 0)
        meridiem = (hour_match.group(3) or "").replace(".", "")
        if meridiem.startswith("p") and hour < 12:
            hour += 12
        elif meridiem.startswith("a") and hour == 12:
            hour = 0
        elif not meridiem and hour <= 7:
            hour += 12  # "at 4" in a work diary means the afternoon
        if 0 <= hour <= 23:
            target = target.replace(hour=hour, minute=minute, second=0, microsecond=0)
    elif matched_day:
        target = target.replace(hour=9, minute=0, second=0, microsecond=0)
    elif not matched_day:
        return None

    return target.isoformat(timespec="seconds")


def _topic_overlap(a: str, b: str) -> float:
    left, right = _topic_tokens(a), _topic_tokens(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _best_topic_match(
    content: str, candidates: list[dict[str, Any]], threshold: float = 0.28
) -> dict[str, Any] | None:
    """The candidate a correction is most plausibly about."""
    ranked = sorted(
        (
            (
                _topic_overlap(content, c.get("source_sentence") or c.get("content") or ""),
                c,
            )
            for c in candidates
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if ranked and ranked[0][0] >= threshold:
        return ranked[0][1]
    return None


def _pick_attribute(sentence: str, memory_type: str) -> str | None:
    if memory_type == "event":
        # A deadline is a scheduled thing, but it is not an appointment: it has
        # exactly one current value per subject, so a later dictation replaces
        # it. Meetings do not work that way, which is why they are told apart
        # here rather than both landing in `meeting_time`.
        if re.search(
            r"\b(deadline|due date|cut ?off|ships?|shipping|launch(?:es|ed|ing)?|"
            r"release date|goes live|by end of)\b",
            sentence,
            re.I,
        ):
            return "deadline"
        if re.search(r"\b(room|zoom|teams|office|floor|call link)\b", sentence, re.I):
            return "meeting_location"
        return "meeting_time"
    for pattern, attribute in _ATTRIBUTE_BY_CUE:
        if pattern.search(sentence):
            return attribute
    if memory_type == "task":
        return "deliverable"
    if memory_type == "preference":
        return "style"
    return None


_RESCHEDULE_RE = re.compile(
    r"^(?:move|moved|moving|push|pushed|shift|shifted|reschedule|rescheduled|change|changed)\s+"
    r"(?:the\s+|our\s+|my\s+)?(?P<who>[A-Z][a-z]+(?:'s)?\s+)?"
    r"(?P<qualifier>(?:[\w-]+\s+){0,3}?)(?P<noun>" + "|".join(_EVENT_NOUNS) + r")\b.*?\bto\s+(?P<when>.+?)\.?$",
    re.IGNORECASE,
)


def _canonicalise(sentence: str, memory_type: str, subject: str | None, value: str | None) -> str:
    """Rewrite a correction into the statement it leaves behind.

    "Move the Rahul meeting to Friday at 4 PM" is an instruction, not a memory.
    Six weeks later the useful thing to have written down is the fact it
    produced: "Meeting with Rahul is on Friday at 4 PM."
    """
    if memory_type != "event":
        return sentence
    stripped = re.sub(
        r"^(?:and|but|so|also|then|actually|ok(?:ay)?|well|right|correction|update)[,\s]+",
        "",
        sentence.strip(),
        flags=re.IGNORECASE,
    )
    match = _RESCHEDULE_RE.match(stripped)
    if not match:
        return sentence
    when = (value or match.group("when") or "").strip().rstrip(".")
    if not when:
        return sentence
    noun = match.group("noun").lower()
    # The person the appointment is with. `subject` was resolved from the named
    # people in the sentence, so it is trusted first: the regex capture happily
    # grabs a project name ("the Beacon empty-state walkthrough with Priya"
    # yields "Beacon"), which would then be written into memory as the person.
    who = (subject or "").strip()
    if not who or who.lower() == "user":
        who = (match.group("who") or "").strip()
        for suffix in ("'s", "’s"):
            if who.endswith(suffix):
                who = who[: -len(suffix)]
        who = who.strip()
    if who.lower() in ("project", "initiative", "program", "workstream"):
        who = ""

    # Keep whatever named the appointment ("Beacon empty-state"). Dropping it
    # would make the memory read well and retrieve badly: a later question about
    # the Beacon walkthrough would find nothing but the word "walkthrough".
    captured_who = (match.group("who") or "").strip()
    qualifier = (match.group("qualifier") or "").strip()
    if captured_who and captured_who.lower() != (who or "").lower():
        qualifier = f"{captured_who} {qualifier}".strip()

    subject_phrase = f"{qualifier} {noun}".strip() if qualifier else noun
    subject_phrase = subject_phrase[0].upper() + subject_phrase[1:]

    if who:
        return f"{subject_phrase} with {who} is on {when}."
    return f"The {subject_phrase} is on {when}."


# Trailing clauses that only make sense in the moment. "Friday at 10 AM, not the
# time I gave before" is a correction; six weeks later the useful memory is just
# "Friday at 10 AM".
_TRAILING_SELF_REFERENCE = re.compile(
    r",\s*(?:not|rather than)\s+(?:what|where|when|the time|the day|the one)\b[^.?!]*",
    re.IGNORECASE,
)


def _clean_sentence(sentence: str) -> str:
    text = re.sub(r"\s+", " ", sentence).strip()
    text = re.sub(
        r"^(?:and|but|so|also|then|actually|ok(?:ay)?|well|right|"
        r"scratch that|correction|update|i meant|make that)[,:\s]+",
        "",
        text,
        flags=re.I,
    )
    text = _TRAILING_SELF_REFERENCE.sub("", text)
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    if text and text[-1] not in ".!?":
        text += "."
    return text


def _self_contained(sentence: str, context_entities: list[str]) -> str:
    """Replace a leading bare pronoun with the entity the paragraph is about.

    A memory that says "He wants the revised numbers" is useless in six weeks.
    """
    if not context_entities:
        return sentence
    people = [e for e in context_entities if not e.lower().startswith(("project", "initiative"))]
    if not people:
        return sentence
    return re.sub(
        r"^(He|She|They)\b",
        people[0],
        sentence,
        count=1,
    )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
@dataclass
class _Candidate:
    sentence: str
    memory_type: str
    confidence: float


class HeuristicEngine(ReasoningEngine):
    """Deterministic engine. No network, no key, no cost, no invention."""

    name = "heuristic"
    model = "heuristic"

    # -- 1. extraction -----------------------------------------------------
    def extract(
        self, *, formatted_text: str, raw_asr: str, timestamp: str, application: str | None
    ) -> ExtractionResult:
        usage = LLMUsage(provider="heuristic", model="heuristic", calls=1)
        text = (formatted_text or "").strip()

        if not text:
            return ExtractionResult(
                decision="IGNORE", rationale="the dictation was empty", usage=usage
            )

        if _FILLER_RE.search(text) and len(content_tokens(text)) <= 8:
            return ExtractionResult(
                decision="IGNORE",
                rationale="thinking aloud - no durable content",
                usage=usage,
            )

        if _DICTATION_MARKERS.search(text):
            return ExtractionResult(
                decision="IGNORE",
                rationale="this is message content being dictated, not a fact about the user's work",
                usage=usage,
            )

        document_entities = _find_entities(text)
        memories: list[ExtractedMemory] = []
        is_correction = bool(_CORRECTION_CUES.search(text))

        for sentence in _split_sentences(text):
            tokens = content_tokens(sentence)
            if len(tokens) < 3:
                continue
            if _FILLER_RE.search(sentence) and len(tokens) <= 6:
                continue

            sentence = _self_contained(sentence, document_entities)
            memory_type, confidence = self._classify(sentence)
            if memory_type is None:
                continue

            entities = _find_entities(sentence) or document_entities
            people = [e for e in entities if not e.lower().startswith(("project", "initiative"))]
            projects = [e for e in entities if e.lower().startswith(("project", "initiative"))]

            phrase = _time_phrase(sentence)
            attribute = _pick_attribute(sentence, memory_type)

            if memory_type == "preference":
                subject = "user"
            elif people:
                # An appointment belongs to the person it is *with*. Without
                # this, "the Beacon empty-state walkthrough with Priya" is filed
                # under Beacon, because that name comes first in the sentence -
                # and a product name becomes a colleague.
                companion = re.search(
                    r"\bwith\s+([A-Z][a-z]{1,20})\b", sentence
                )
                if companion and companion.group(1) in people:
                    subject = companion.group(1)
                else:
                    subject = people[0]
            elif projects:
                subject = projects[0]
            else:
                subject = None

            value = phrase if attribute in ("meeting_time", "deadline") else None

            # "Kenji asked whether key rotation affects the access review" trips
            # the event noun "review" but names no time, so it is not a scheduled
            # thing at all. Letting it hold the meeting_time slot puts a valueless
            # memory in front of every real reschedule for that person - which is
            # what made the false supersede above possible.
            if attribute in ("meeting_time", "deadline") and not phrase:
                attribute = None
                if memory_type == "event":
                    memory_type = "episode"
            if value is None and attribute == "role":
                role_match = re.search(
                    r"\b(?:is|as)\s+(?:the\s+)?([a-z][a-z\s]{2,30}?)(?:\s+(?:for|on|of|at)\b|[.,]|$)",
                    sentence,
                    re.IGNORECASE,
                )
                if role_match:
                    value = role_match.group(1).strip()
                # Deliberately no fallback here. "Kenji owns the infrastructure
                # work" states a role but names none, and filling the slot with
                # a placeholder like "lead" invents a value that then
                # contradicts the real one ("infrastructure engineer") the next
                # time the user says it. An unnamed role stays unnamed.
            if value is None and attribute == "project" and projects:
                value = projects[0]
            if value is None and memory_type == "preference":
                # The style word is the slot value, so a later "actually, make
                # them detailed" can supersede an earlier "keep them short".
                style = re.search(
                    r"\b(short|brief|concise|tight|punchy|long|detailed|thorough|"
                    r"formal|informal|professional|casual|bullet ?points?)\b",
                    sentence,
                    re.IGNORECASE,
                )
                if style:
                    value = style.group(1).lower()

            # A memory with nobody and nothing named in it is very hard to use
            # six weeks later, so Kivi trusts it less - and below the threshold
            # it is stored as REJECTED rather than acted on.
            if not entities and memory_type == "episode":
                confidence -= 0.22
            if is_correction:
                confidence = min(0.97, confidence + 0.08)

            memories.append(
                ExtractedMemory(
                    type=memory_type,
                    content=_clean_sentence(_canonicalise(sentence, memory_type, subject, value)),
                    subject=subject,
                    attribute=attribute,
                    value=value,
                    entities=entities,
                    tags=self._tags(sentence, memory_type, is_correction),
                    confidence=round(confidence, 2),
                    occurred_at=_resolve_occurred_at(phrase, timestamp),
                    source_sentence=sentence,
                )
            )

        if not memories:
            return ExtractionResult(
                decision="IGNORE",
                rationale="nothing in this dictation matched a durable memory shape",
                usage=usage,
            )

        # Two memories with the same text inside one dictation help nobody.
        deduped: list[ExtractedMemory] = []
        seen: set[str] = set()
        for memory in memories:
            key = normalise(memory.content)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(memory)

        kinds = ", ".join(sorted({m.type for m in deduped}))
        return ExtractionResult(
            decision="REMEMBER",
            rationale=f"found {len(deduped)} durable memory/memories ({kinds})",
            memories=deduped,
            usage=usage,
        )

    def _classify(self, sentence: str) -> tuple[str | None, float]:
        """Assign a memory type and a confidence, or None to drop the sentence."""
        has_event = bool(_EVENT_RE.search(sentence))
        has_time = _time_phrase(sentence) is not None

        if _PREFERENCE_CUES.search(sentence):
            return "preference", 0.86
        # "I promised Kenji I would review the Ember checklist before Wednesday"
        # is a commitment, not an appointment - even though "review" is an event
        # noun and "Wednesday" is a time. Checking this first stops promises
        # being filed as meetings and then swallowed as duplicate reschedules.
        if _COMMITMENT_RE.search(sentence):
            return "task", 0.88
        if has_event and has_time:
            return "event", 0.92
        if _TASK_CUES.search(sentence):
            return "task", 0.84
        if has_event:
            return "event", 0.72
        # "The Delta release ships on the 21st" is a scheduled thing even though
        # it never says "meeting". A named subject plus a resolvable time is
        # enough - and without this rule short sentences like it fall through
        # the episode length gate and are lost entirely.
        if has_time and _find_entities(sentence):
            return "event", 0.80
        if _FACT_RE.search(sentence) and re.search(
            r"\b(leads?|heads?|runs?|owns?|manages?|reports? to|works? (?:on|with|at)|"
            r"is (?:the|our|my)|joined|handles?)\b",
            sentence,
            re.IGNORECASE,
        ):
            return "fact", 0.88
        # "Discussed the roadmap with Wren." is three content tokens after
        # stopwords, so the length gate below dropped it - yet it is exactly the
        # episode the product is meant to keep. A discussion verb plus a named
        # person is enough on its own; length is only a proxy for substance, and
        # a bad one when someone dictates tersely.
        if _DISCUSSION_RE.search(sentence) and _find_entities(sentence):
            return "episode", 0.7
        if len(content_tokens(sentence)) >= 5:
            return "episode", 0.62
        return None, 0.0

    def _tags(self, sentence: str, memory_type: str, is_correction: bool) -> list[str]:
        tags = [memory_type]
        if is_correction:
            tags.append("correction")
        for keyword in ("pricing", "budget", "hiring", "launch", "design", "security", "roadmap"):
            if re.search(rf"\b{keyword}\b", sentence, re.IGNORECASE):
                tags.append(keyword)
        return tags

    # -- 2. resolution -----------------------------------------------------
    def resolve(
        self, *, new_memory: dict[str, Any], candidates: list[dict[str, Any]]
    ) -> ResolutionDecision:
        usage = LLMUsage(provider="heuristic", model="heuristic", calls=1)
        if not candidates:
            return ResolutionDecision(
                action="NEW", reason="no memory occupies this slot yet", usage=usage
            )

        new_value = normalise(new_memory.get("value") or "")
        new_content = normalise(new_memory.get("content") or "")
        # Topic comparison uses the words the user actually said.
        new_source = new_memory.get("source_sentence") or new_memory.get("content") or ""
        attribute = (new_memory.get("attribute") or "").lower()
        is_correction = "correction" in (new_memory.get("tags") or [])

        # Exact restatement.
        for candidate in candidates:
            if normalise(candidate.get("content") or "") == new_content:
                return ResolutionDecision(
                    action="DUPLICATE",
                    target_memory_id=candidate["id"],
                    reason="the dictation restates a memory already held, word for word",
                    usage=usage,
                )

        if not attribute or attribute not in SINGLE_VALUED_ATTRIBUTES:
            return ResolutionDecision(
                action="NEW",
                reason="this memory adds to what is known rather than replacing a single-valued slot",
                usage=usage,
            )

        same_slot = [
            c
            for c in candidates
            if (c.get("attribute") or "").lower() == attribute
            and normalise(c.get("subject") or "") == normalise(new_memory.get("subject") or "")
        ]
        if not same_slot:
            return ResolutionDecision(
                action="NEW", reason="no existing memory fills this slot", usage=usage
            )

        # Same slot, same value: nothing new to store.
        for candidate in same_slot:
            if new_value and normalise(candidate.get("value") or "") == new_value:
                return ResolutionDecision(
                    action="DUPLICATE",
                    target_memory_id=candidate["id"],
                    reason=f"{attribute} is already recorded as {new_value!r}",
                    usage=usage,
                )

        new_content = new_memory.get("content") or ""

        # A meeting slot needs the two memories to be about the same thing
        # before either can replace the other. Two unrelated meetings with the
        # same person are two meetings, not a reschedule.
        if attribute in RESCHEDULABLE_ATTRIBUTES:
            new_projects = _project_entities(new_memory)

            def project_compatible(candidate: dict[str, Any]) -> bool:
                candidate_projects = _project_entities(candidate)
                if not new_projects or not candidate_projects:
                    return True
                return bool(new_projects & candidate_projects)

            topic_pool = [c for c in same_slot if project_compatible(c)]
            same_thing = [
                c
                for c in topic_pool
                if _same_topic(new_source, c.get("source_sentence") or c.get("content") or "")
            ]
            if is_correction:
                # A correction is terse - "push the Aditi Cobalt schema check-in
                # to Thursday" shares few words with the sentence it corrects -
                # so the target is whichever memory it overlaps most, and only
                # the most recent one when nothing overlaps at all.
                # If nothing plausibly matches, this correction is about
                # something Kivi never recorded, and the honest outcome is simply
                # a new memory. Falling back to "the most recent thing in this
                # slot" silently retires an unrelated one: a push of a Beacon
                # check-in once superseded "Kenji asked whether key rotation
                # affects the access review", which is not the same appointment
                # by any reading.
                target = _best_topic_match(new_source, topic_pool) or (
                    same_thing[0] if same_thing else None
                )
                if target is None:
                    return ResolutionDecision(
                        action="NEW",
                        reason=(
                            "a correction, but nothing stored matches what it corrects - "
                            "recording it rather than retiring an unrelated memory"
                        ),
                        usage=usage,
                    )
                return ResolutionDecision(
                    action="SUPERSEDES",
                    target_memory_id=target["id"],
                    reason=(
                        "the user explicitly corrected this - the earlier value is no longer "
                        "current"
                    ),
                    usage=usage,
                )
            if not same_thing:
                return ResolutionDecision(
                    action="NEW",
                    reason=(
                        f"a different appointment with the same person; "
                        f"it does not replace what is already known"
                    ),
                    usage=usage,
                )
            return ResolutionDecision(
                action="CONFLICTS",
                target_memory_id=same_thing[0]["id"],
                reason=(
                    "two times for what looks like the same appointment, with nothing marking "
                    "either as current; keeping both so Kivi can say it is unsure"
                ),
                usage=usage,
            )

        target = same_slot[0]  # candidates arrive newest first

        if is_correction:
            return ResolutionDecision(
                action="SUPERSEDES",
                target_memory_id=target["id"],
                reason="the user explicitly corrected this - the earlier value is no longer current",
                usage=usage,
            )

        newer = _is_newer(new_memory.get("timestamp"), target.get("timestamp"))
        if newer and attribute in VOLATILE_ATTRIBUTES:
            return ResolutionDecision(
                action="SUPERSEDES",
                target_memory_id=target["id"],
                reason=f"a later dictation gives a different {attribute}; treating it as an update",
                usage=usage,
            )

        return ResolutionDecision(
            action="CONFLICTS",
            target_memory_id=target["id"],
            reason=(
                f"two different values for {attribute} and nothing marks either as current; "
                f"keeping both so Kivi can say it is unsure"
            ),
            usage=usage,
        )

    # -- 3. answering ------------------------------------------------------
    def answer(
        self, *, question: str, memories: list[dict[str, Any]], plan: Any, now: str | None = None
    ) -> AnswerResult:
        usage = LLMUsage(provider="heuristic", model="heuristic", calls=1)
        intent = getattr(plan, "intent", "general")
        entities = list(getattr(plan, "entities", []))
        residuals = list(getattr(plan, "residual_tokens", []))

        # A rescued dictation is not ACTIVE - it is not a memory at all - but it
        # is the only thing retrieval found, and refusing to read it would waste
        # the rescue. It answers, with the caveat attached below.
        rescued = [m for m in memories if m.get("from_transcript")]
        if rescued:
            # A rescue is held to exactly the same standard as a memory: the
            # topic of the question must actually appear in the text. Without
            # this, BM25 returns its best match for *any* question - it always
            # returns something - and "what is my bank account number" comes
            # back with whatever dictation shares the most common words. The
            # rescue exists to recover content, never to lower the bar.
            missing = _unsupported_tokens(rescued, residuals) if residuals else []
            if missing:
                return AnswerResult(
                    answer=_abstention_sentence(entities, topic=missing[0]),
                    abstained=True,
                    supported=False,
                    confidence=0.0,
                    reasoning=(
                        f"no memory matched, and the raw dictations do not mention "
                        f"{', '.join(missing)} either"
                    ),
                    usage=usage,
                )
            if entities and not _mentions_any_entity(rescued, entities):
                return AnswerResult(
                    answer=_abstention_sentence(entities),
                    abstained=True,
                    supported=False,
                    confidence=0.0,
                    reasoning=(
                        f"no memory matched, and no raw dictation mentions "
                        f"{', '.join(entities)}"
                    ),
                    usage=usage,
                )
            answer = " ".join(m["content"] for m in rescued[:2])
            return AnswerResult(
                answer="I hadn't recorded this as something I know, but you said: " + answer,
                abstained=False,
                supported=True,
                confidence=0.45,
                reasoning=(
                    "no memory matched, so this is answered from the raw dictation - "
                    "nothing has reconciled it against anything said later"
                ),
                used_memory_ids=[m["id"] for m in rescued[:2]],
                usage=usage,
            )

        active = [m for m in memories if m.get("status", "ACTIVE") == "ACTIVE"]

        if not active:
            return AnswerResult(
                answer=_abstention_sentence(entities),
                abstained=True,
                supported=False,
                confidence=0.0,
                reasoning="retrieval returned nothing above the relevance threshold",
                usage=usage,
            )

        # --- is the question's topic actually supported by memory? --------
        if entities and not _mentions_any_entity(active, entities):
            return AnswerResult(
                answer=_abstention_sentence(entities),
                abstained=True,
                supported=False,
                confidence=0.0,
                reasoning=f"nothing in memory mentions {', '.join(entities)}",
                usage=usage,
            )

        missing = _unsupported_tokens(active, residuals) if residuals else []
        if missing:
            known = _known_summary(active, entities)
            answer = _abstention_sentence(entities, topic=missing[0])
            if known:
                answer += f" {known}"
            return AnswerResult(
                answer=answer,
                abstained=True,
                supported=False,
                confidence=0.0,
                reasoning=(
                    f"the question asks about {', '.join(missing)}, which appears in no "
                    f"stored memory"
                ),
                usage=usage,
            )

        # --- conflict? ----------------------------------------------------
        conflict_group = _find_conflict(active, intent, entities)
        if conflict_group:
            attribute, conflicting = conflict_group
            values = [m.get("value") or m["content"] for m in conflicting]
            answer = (
                f"I found {len(conflicting)} different answers in your history and I'm not "
                f"confident which one is current: "
                + "; ".join(f"“{v}”" for v in values)
                + ". You may want to confirm which one still stands."
            )
            return AnswerResult(
                answer=answer,
                used_memory_ids=[m["id"] for m in conflicting],
                conflict=True,
                supported=True,
                confidence=0.35,
                reasoning=(
                    f"two active memories give different values for {attribute} and neither "
                    f"supersedes the other"
                ),
                usage=usage,
            )

        # --- compose ------------------------------------------------------
        selected = _select_for_intent(active, intent)
        if not selected:
            selected = active[:2]

        if intent == INTENT_DRAFT:
            answer, used = _compose_draft(selected, entities)
        elif intent == INTENT_WHY:
            answer, used = _compose_provenance(selected)
        else:
            answer, used = _compose_statement(selected, intent, entities)

        confidence = round(
            min(0.92, 0.45 + 0.12 * len(used) + 0.15 * (1 if intent != "general" else 0)), 2
        )
        return AnswerResult(
            answer=answer,
            used_memory_ids=used,
            abstained=False,
            conflict=False,
            supported=True,
            confidence=confidence,
            reasoning=(
                f"intent={intent}; answered from {len(used)} memory/memories, "
                f"every sentence taken from stored memory text"
            ),
            usage=usage,
        )


# ---------------------------------------------------------------------------
# Answer composition
# ---------------------------------------------------------------------------
def _abstention_sentence(entities: list[str], topic: str | None = None) -> str:
    if topic and entities:
        return (
            f"I don't have anything about {entities[0]}'s {topic} in your history."
        )
    if topic:
        return f"I don't have anything about {topic} in your history."
    if entities:
        return f"I don't have anything about {entities[0]} in your history."
    return "I don't have that information in your history."


def _known_summary(memories: list[dict[str, Any]], entities: list[str]) -> str:
    """Offer what Kivi *does* know, so an abstention is still useful."""
    if not entities:
        return ""
    relevant = [m for m in memories if _mentions_entity(m, entities[0])][:1]
    if not relevant:
        return ""
    return f"What I do have: {relevant[0]['content']}"


def _mentions_entity(memory: dict[str, Any], entity: str) -> bool:
    haystack = normalise(
        " ".join(
            [
                memory.get("content") or "",
                memory.get("subject") or "",
                " ".join(memory.get("entities") or []),
            ]
        )
    )
    return normalise(entity) in haystack


def _mentions_any_entity(memories: list[dict[str, Any]], entities: list[str]) -> bool:
    return any(_mentions_entity(m, e) for m in memories for e in entities)


def _unsupported_tokens(memories: list[dict[str, Any]], tokens: Iterable[str]) -> list[str]:
    """Which of the question's topic words appear nowhere in the retrieved memory.

    Every topic word must be found, not merely one of them. "What is Sarah's
    phone number" and "what are Sarah's revised numbers" share the word
    "number"; requiring only one match makes the first question borrow the
    second's evidence and answer confidently about something Kivi has never
    heard. Matching is stem-based, so "leads" in a question still finds "lead"
    in a memory.
    """
    corpus: set[str] = set()
    for memory in memories:
        corpus.update(content_tokens(memory.get("content") or ""))
        corpus.update(content_tokens(" ".join(memory.get("tags") or [])))
        corpus.update(content_tokens(memory.get("value") or ""))
        corpus.update(content_tokens(memory.get("subject") or ""))

    missing: list[str] = []
    for token in tokens:
        stem = token[:5]
        if not any(word.startswith(stem) or token.startswith(word[:5]) for word in corpus):
            missing.append(token)
    return missing


# A conflict is only worth raising when it is about the thing being asked.
# Without this scoping, an ambiguous meeting time would derail a question about
# what the user owes someone - Kivi would announce a disagreement nobody asked
# about and never give the answer.
_CONFLICT_ATTRIBUTES_BY_INTENT: dict[str, frozenset[str]] = {
    INTENT_WHEN: frozenset({"meeting_time", "deadline", "meeting_location"}),
    INTENT_WHO: frozenset({"role"}),
    INTENT_PREFERENCE: frozenset({"email_style", "summary_style", "tone", "style"}),
}


def _find_conflict(
    memories: list[dict[str, Any]], intent: str, entities: list[str]
) -> tuple[str, list[dict[str, Any]]] | None:
    """Two active memories claiming different values for the slot being asked about."""
    relevant = _CONFLICT_ATTRIBUTES_BY_INTENT.get(intent)
    if not relevant:
        return None

    # Grouped by attribute alone, not by (subject, attribute). Which entity a
    # sentence is "about" is a parser judgement - "Sarah has the Atlas sign-off
    # on Friday" and "I have the Atlas sign-off with Sarah on Monday" get filed
    # under different subjects - and a real disagreement should not go
    # unreported because of it. The entity filter above already scopes the set
    # to what was asked about; the same-topic check below does the rest.
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for memory in memories:
        attribute = (memory.get("attribute") or "").lower()
        if attribute not in relevant or not memory.get("value"):
            continue
        # A conflict about someone the question did not mention is not this
        # question's problem.
        if entities and not any(_mentions_entity(memory, e) for e in entities):
            continue
        # A role belongs to a person: "Kenji is the infrastructure engineer" and
        # "Omar is the recruiter" are two facts about one project, not two
        # answers to one question. Times and locations belong to the *event*,
        # whose subject the parser may record inconsistently, so those group on
        # the attribute alone and rely on the topic check below.
        key = normalise(memory.get("subject") or "") if attribute in PERSON_SCOPED_ATTRIBUTES else ""
        groups.setdefault((key, attribute), []).append(memory)

    # A disagreement is only worth raising if it is about the memory Kivi would
    # otherwise have answered with. Without this, a stale contradiction buried
    # at rank seven derails a question it has nothing to do with - Kivi
    # announces a conflict about Kenji's job title when asked who the finance
    # lead is.
    best_id = memories[0]["id"] if memories else None

    for (_, attribute), group in groups.items():
        cluster = _same_topic_cluster(group)
        values = {normalise(m.get("value") or "") for m in cluster}
        if len(values) > 1 and any(m["id"] == best_id for m in cluster):
            return attribute, cluster[:3]
    return None


def _same_topic_cluster(group: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The memories in a group that are about the same thing as the best one.

    Retrieval hands back everything relevant to the question, which for a busy
    person is several different appointments. Only the ones describing the *same*
    appointment can disagree with each other - the rest are just a calendar.
    """
    if len(group) < 2:
        return group
    pivot = group[0]
    pivot_text = pivot.get("content") or ""
    cluster = [pivot]
    for memory in group[1:]:
        if _same_topic(pivot_text, memory.get("content") or "", threshold=0.34):
            cluster.append(memory)
    return cluster


_TYPE_ORDER_BY_INTENT: dict[str, tuple[str, ...]] = {
    INTENT_WHEN: ("event", "episode", "task"),
    INTENT_WHO: ("fact", "episode"),
    INTENT_PREPARE: ("task", "event", "episode", "fact"),
    INTENT_DISCUSSED: ("episode", "task", "event"),
    INTENT_PREFERENCE: ("preference",),
    INTENT_DRAFT: ("preference", "event", "task"),
    # INTENT_WHY is deliberately absent: "why do you think X" is answered by
    # whichever memory actually supports X, and forcing episodes to the front
    # makes Kivi cite a vaguely related anecdote over the exact commitment.
}

_LEAD_IN: dict[str, str] = {
    INTENT_WHEN: "",
    INTENT_WHO: "",
    INTENT_PREPARE: "Here's what you have outstanding:",
    INTENT_DISCUSSED: "From your recent dictations:",
    INTENT_PREFERENCE: "",
    INTENT_WHY: "",
}


# How many memories an answer should draw on. A "when is my meeting" question
# wants one sentence, not a digest of everything Kivi knows about that person.
_ANSWER_WIDTH: dict[str, int] = {
    INTENT_WHEN: 1,
    INTENT_WHO: 1,
    INTENT_PREFERENCE: 2,
    INTENT_PREPARE: 4,
    INTENT_DISCUSSED: 3,
    INTENT_WHY: 1,
}


def _select_for_intent(memories: list[dict[str, Any]], intent: str) -> list[dict[str, Any]]:
    width = _ANSWER_WIDTH.get(intent, 3)
    preferred = _TYPE_ORDER_BY_INTENT.get(intent)
    if not preferred:
        return memories[:width]

    ordered: list[dict[str, Any]] = []
    for memory_type in preferred:
        ordered.extend([m for m in memories if m.get("type") == memory_type])

    # A narrow question answered from the right type of memory should stop
    # there. Only widen when the preferred types turned up nothing.
    if ordered:
        return ordered[:width]
    return memories[:width]


def _compose_statement(
    memories: list[dict[str, Any]], intent: str, entities: list[str]
) -> tuple[str, list[int]]:
    """Assemble an answer out of stored memory sentences only.

    Nothing here writes a new fact: every clause is memory text. That is the
    whole point - this engine is structurally incapable of hallucinating.
    """
    used = memories
    sentences = [m["content"].rstrip(".") + "." for m in used]

    lead = _LEAD_IN.get(intent, "")
    if intent == INTENT_PREPARE and entities:
        lead = f"For {entities[0]}, here's what your history has:"
    elif intent == INTENT_DISCUSSED and entities:
        lead = f"On {entities[0]}, from your recent dictations:"

    body = " ".join(sentences)
    answer = f"{lead} {body}".strip() if lead else body
    return answer, [m["id"] for m in used]


def _compose_provenance(memories: list[dict[str, Any]]) -> tuple[str, list[int]]:
    """Answer a 'why do you think that?' question by pointing at the source."""
    memory = memories[0]
    date = (memory.get("timestamp") or "")[:16].replace("T", " ")
    excerpt = memory.get("source_excerpt") or memory["content"]
    app = memory.get("application")
    where = f" in {app}" if app else ""
    answer = (
        f'Because of what you dictated on {date}{where}: "{excerpt}" '
        f"That's memory #{memory['id']}, from transcript "
        f"#{memory.get('source_transcript_id')}."
    )
    return answer, [memory["id"]]


# Turn a memory sentence into a clause that reads naturally inside a message.
_FIRST_PERSON_RE = re.compile(r"^I (?:need to|have to|will|'ll|must|should)\s+", re.IGNORECASE)


def _as_clause(sentence: str) -> str:
    """Rephrase a stored memory as something you would write to someone else."""
    text = sentence.strip().rstrip(".")
    promise = _FIRST_PERSON_RE.match(text)
    if promise:
        return "I'll " + text[promise.end() :]
    if text[:1].isupper() and not re.match(r"^(I|[A-Z][a-z]+\b)", text):
        return text[0].lower() + text[1:]
    return text


def _compose_draft(memories: list[dict[str, Any]], entities: list[str]) -> tuple[str, list[int]]:
    """Draft a short message, obeying any stored preference about style."""
    recipient = entities[0] if entities else "there"
    preferences = [m for m in memories if m.get("type") == "preference"]
    substance = [m for m in memories if m.get("type") in ("event", "task", "episode")][:2]

    concise = any(
        re.search(r"\b(short|brief|concise|tight|to the point|punchy)\b", m["content"], re.I)
        for m in preferences
    )

    clauses = [_as_clause(m["content"]) for m in substance]
    if clauses:
        body = ". ".join(c[0].upper() + c[1:] for c in clauses)
        message = f"Hi {recipient} — {body}."
    else:
        message = f"Hi {recipient} — following up on the below."

    if not concise:
        message += " Let me know if anything has changed at your end."

    lines = [f"Here's a draft for {recipient}:", "", f'    "{message}"']
    if preferences:
        style = "short" if concise else "in your usual style"
        lines += [
            "",
            f"Kept {style}, following what you told Kivi: "
            f"\"{preferences[0]['content'].rstrip('.')}.\"",
        ]

    used = [m["id"] for m in preferences + substance]
    return "\n".join(lines), used


def _is_newer(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return str(a) > str(b)  # ISO-8601 sorts lexicographically
