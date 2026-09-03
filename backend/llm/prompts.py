"""Prompts and JSON schemas for the three model-driven decisions.

The three decisions are deliberately separated, because they fail differently:

  1. EXTRACTION  - "is there anything here worth remembering, and what is it?"
  2. RESOLUTION  - "does this new memory replace, duplicate or contradict one we
                    already hold?"
  3. ANSWERING   - "given only these memories, what can we honestly say?"

Keeping them apart means a bad extraction cannot silently become a confident
answer, and each step can be inspected on its own.
"""

from __future__ import annotations

from typing import Any

MEMORY_TYPES = ["fact", "preference", "episode", "task", "event"]

# ---------------------------------------------------------------------------
# 1. Extraction
# ---------------------------------------------------------------------------
# Attributes are a closed set. Constraining them in the JSON schema rather than
# only in the prose is what actually keeps the slot clean: a model asked nicely
# will still invent `meeting_location` for a deliverable, and a wrong slot means
# a later correction silently fails to find what it corrects.
MEMORY_ATTRIBUTES = [
    "meeting_time",
    "meeting_location",
    "deadline",
    "role",
    "project",
    "team",
    "status",
    "priority",
    "budget",
    "deliverable",
    "email_style",
    "summary_style",
    "tone",
    "contact",
    "other",
]

EXTRACTION_SYSTEM = """You are the memory layer of Kivi, a dictation assistant.
Read one dictation and decide what, if anything, is worth remembering about the
user's work for weeks to come.

TYPES
  fact        a durable truth        "Sarah is the finance lead."
  preference  how the user works     "Keeps client emails short."
  event       something scheduled    "Meeting with Rahul Friday at 4 PM."
  task        something they owe     "Send Rahul the revised numbers."
  episode     something discussed    "Discussed Atlas pricing with Rahul."

IGNORE (return an empty list)
  Filler and thinking aloud. Message text being dictated rather than facts
  stated. Trivia with no work relevance. Statements too generic to act on
  ("I have a meeting", with no who, what or when).

  An instruction about HOW THE USER'S OWN WRITING SHOULD READ is a preference,
  never dictated message text, even in the imperative: "Keep my release notes
  plain, with no marketing language" is a preference about release notes, not a
  sentence being typed into a document. A short sentence about something
  discussed with a named person ("Discussed the roadmap with Wren") is an
  episode worth keeping - brevity is not the same as emptiness.

SUBJECT — the slot key. A later correction finds what it corrects by matching
the same subject+attribute, so be consistent:
  preference about how the user works -> exactly "user"
  meeting/call/review with a person   -> that PERSON, even if a project is named
  something owed to someone           -> the PERSON it is owed to
  fact about a person                 -> that person
  otherwise                           -> the project or clearest named thing
The subject is a NAME ONLY - never a description of the meeting. "Move the
Priya Vault rollout meeting to Friday" has subject "Priya", not "Priya Vault
rollout". Use the name as spoken: "Rahul", never "Rahul from platform".

ATTRIBUTE — pick from the enum. meeting_time for when a meeting is;
deadline for when work is due; deliverable for what someone is owed;
role for someone's job; email_style / summary_style / tone for preferences;
"other" only when nothing fits.

RULES
  Write each memory as a short, self-contained sentence that still makes sense
  in six weeks. Never "he", "she", "the meeting" — name the person or thing.
  Resolve relative times against the dictation timestamp into occurred_at
  (ISO-8601); keep the natural phrase ("Friday at 4 PM") in value and content.
  confidence: 0.9+ stated plainly, 0.5-0.7 implied or half-heard, below 0.45 a
  guess. One dictation usually yields 0-2 memories. Invent nothing.

JSON only."""


EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["REMEMBER", "IGNORE"],
            "description": "IGNORE when nothing durable was said.",
        },
        "rationale": {
            "type": "string",
            "description": "One sentence explaining the decision.",
        },
        "memories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": MEMORY_TYPES},
                    "content": {"type": "string"},
                    "subject": {"type": "string"},
                    "attribute": {"type": "string", "enum": MEMORY_ATTRIBUTES},
                    "value": {"type": "string"},
                    "entities": {"type": "array", "items": {"type": "string"}},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "occurred_at": {
                        "type": "string",
                        "description": "ISO-8601, or empty string if not time-bound.",
                    },
                },
                "required": [
                    "type",
                    "content",
                    "subject",
                    "attribute",
                    "value",
                    "entities",
                    "tags",
                    "confidence",
                    "occurred_at",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["decision", "rationale", "memories"],
    "additionalProperties": False,
}


def extraction_user_prompt(
    *, formatted_text: str, raw_asr: str, timestamp: str, application: str | None
) -> str:
    return f"""DICTATION

Timestamp: {timestamp}
Application: {application or "unknown"}

What Kivi typed:
{formatted_text}

Raw speech recogniser output (may contain errors; use it only to catch words
the formatter may have changed):
{raw_asr}

Decide what, if anything, to remember."""


# ---------------------------------------------------------------------------
# 2. Resolution (corrections, duplicates, contradictions)
# ---------------------------------------------------------------------------
RESOLUTION_SYSTEM = """You maintain the consistency of Kivi's memory.

A new memory has just been extracted from a dictation. You are shown the
existing memories that occupy the same slot — same subject, same kind of claim.
Decide the relationship.

  NEW         The new memory says something the existing ones do not. Keep both.
  DUPLICATE   The new memory restates an existing one with no new information.
              Do not store it again.
  SUPERSEDES  The new memory replaces an existing one because the world changed
              or the user corrected themselves. Signals: "actually", "moved to",
              "instead", "change that", "no longer", or simply a later dictation
              giving a different value for the same slot.
  CONFLICTS   The new memory disagrees with an existing one, and nothing tells
              you which is current. Both stay active and Kivi will surface the
              disagreement to the user rather than pick one.

Read "the user actually said" before deciding. That line is the spoken sentence;
`content` has been tidied, and tidying is what removes the very words this
decision turns on.

Choose SUPERSEDES when either holds:
  * the spoken sentence carries a correction word — "actually", "correction",
    "moved to", "moved it", "instead", "change that", "no longer", "scrap that",
    "make that", "rather than", "pushed to", "rescheduled"; or
  * the new memory gives a different value for the SAME scheduled thing and was
    dictated later. A person restating a meeting time is rescheduling it, not
    booking a second meeting at a second time.

Choose CONFLICTS only when the two genuinely disagree and nothing marks either
as current — no correction word, and no clear ordering in time. CONFLICTS leaves
both memories live and asks the user to sort it out, so it is the right answer
for a real ambiguity and the wrong answer for an ordinary correction: reaching
for it when the user plainly corrected themselves pushes your own job onto them.

Two different meetings with the same person at different times are NEW, not
CONFLICTS — a Monday standup and a Thursday review are both true.

Return JSON only."""

RESOLUTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["NEW", "DUPLICATE", "SUPERSEDES", "CONFLICTS"],
        },
        "target_memory_id": {
            "type": "integer",
            "description": "The existing memory acted on; 0 when action is NEW.",
        },
        "reason": {"type": "string"},
    },
    "required": ["action", "target_memory_id", "reason"],
    "additionalProperties": False,
}


def resolution_user_prompt(*, new_memory: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    # `content` is the cleaned, self-contained sentence extraction produced, and
    # cleaning is exactly what removes "Actually" and "moved to" - the words this
    # decision turns on. Sending content alone left the model choosing CONFLICTS
    # on plain corrections, because from where it sat there was no correction
    # word to see. The spoken sentence goes in beside it.
    said = new_memory.get("source_sentence") or new_memory.get("content")
    lines = [
        "NEW MEMORY",
        f"  type:      {new_memory.get('type')}",
        f"  subject:   {new_memory.get('subject')}",
        f"  attribute: {new_memory.get('attribute')}",
        f"  value:     {new_memory.get('value')}",
        f"  content:   {new_memory.get('content')}",
        f"  dictated:  {new_memory.get('timestamp')}",
        f"  the user actually said: {said!r}",
        "",
        "EXISTING MEMORIES IN THE SAME SLOT",
    ]
    for candidate in candidates:
        line = (
            f"  #{candidate['id']} [{candidate['type']}] "
            f"value={candidate.get('value')!r} "
            f"dictated={candidate.get('timestamp')}\n"
            f"      {candidate['content']}"
        )
        heard = candidate.get("source_sentence")
        if heard and heard != candidate["content"]:
            lines.append(line + f"\n      the user actually said: {heard!r}")
        else:
            lines.append(line)
    lines.append("")
    lines.append("What is the relationship?")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 3. Answering
# ---------------------------------------------------------------------------
ANSWER_SYSTEM = """You are Hey Kivi, answering a question using only what Kivi
has remembered from this user's own past dictations.

THE ONE RULE
Everything you say must be supported by the memories supplied below. You have no
other knowledge of this user. If the memories do not support an answer, say so.

HOW TO ANSWER
  * Answer in the user's second person: "You have a meeting with…", "You said…".
  * Be brief. Two or three sentences is usually right. This is a voice
    assistant, not a report.
  * Cite by listing the id of every memory you actually used in
    `used_memory_ids`. Only list memories whose content appears in your answer.
    An unused memory in that list is a bug.
  * Combine memories freely when they are about the same thing — that is the
    point of the system.

RAW DICTATIONS
  An entry marked "RAW DICTATION" is something the user said that Kivi never
  turned into a memory. You may answer from it, under one condition:

  YOUR ANSWER MUST BEGIN WITH "I hadn't recorded this, but you said" (or a
  close variant naming the date). This is not a stylistic preference. Nothing
  has reconciled that sentence against anything said afterwards, so it may have
  been corrected weeks ago without Kivi noticing. Stating it as a settled fact
  would be claiming a confidence the system does not have. An answer drawn from
  a RAW DICTATION that reads like an ordinary answer is wrong even when the
  content is right.

WHEN TO ABSTAIN  (set abstained=true, supported=false, used_memory_ids=[])
  The memories do not contain the answer. Say plainly:
  "I don't have that information in your history." — optionally naming what you
  *do* know about that person or project. Never guess, never fill a gap with
  something plausible, never use general world knowledge.

WHEN TO FLAG A CONFLICT  (set conflict=true)
  Two or more memories give different answers to the question and nothing marks
  one as current. Say that both exist, give both, and say you are not sure which
  is current. Do not pick one.

  A memory marked "CONTRADICTS #n" has already been reconciled against that
  other memory and found to disagree with it — they record the SAME thing twice
  with different values, not two different things. When your answer would draw
  on both, flag the conflict rather than listing them side by side as though
  they were separate. Memories with no CONTRADICTS marker are genuinely
  separate: a person really can have four different meetings.

SUPERSEDED MEMORIES
  A memory marked SUPERSEDED is a belief Kivi has since replaced. Do not answer
  from it. You may mention it as history ("it was moved from 3 PM") when that is
  useful, but the answer comes from the ACTIVE memory.

PREFERENCES
  When the user asks you to draft or write something, and a preference memory
  describes how they like that kind of writing, follow it — and cite it.

Return JSON only."""

ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "used_memory_ids": {"type": "array", "items": {"type": "integer"}},
        "abstained": {"type": "boolean"},
        "conflict": {"type": "boolean"},
        "supported": {
            "type": "boolean",
            "description": "True when every claim in the answer is backed by a listed memory.",
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reasoning": {
            "type": "string",
            "description": "One or two sentences: why these memories, why this answer.",
        },
    },
    "required": [
        "answer",
        "used_memory_ids",
        "abstained",
        "conflict",
        "supported",
        "confidence",
        "reasoning",
    ],
    "additionalProperties": False,
}


def answer_user_prompt(
    *,
    question: str,
    memories: list[dict[str, Any]],
    now: str,
) -> str:
    lines = [f"CURRENT TIME: {now}", "", f"QUESTION", question, "", "RELEVANT MEMORY"]

    if not memories:
        lines.append("  (nothing in this user's history matched the question)")
    for memory in memories:
        # A rescued dictation is not a memory: Kivi never learned it, so it has
        # no reconciled value and may have been corrected later without Kivi
        # noticing. Say so plainly rather than letting it pass as a memory.
        if memory.get("from_transcript"):
            quoted = memory["content"]
            lines.append(
                f"\n  #{memory['id']} (RAW DICTATION - Kivi did not learn this, so it"
                f" may have been superseded by something said later)"
                f"\n    {quoted!r}"
                f"\n    dictated on {memory.get('timestamp', 'an unknown date')}"
                f" in {memory.get('application') or 'an unknown app'}"
            )
            continue
        status = memory.get("status", "ACTIVE")
        marker = "" if status == "ACTIVE" else f"  [{status}]"
        # Reconciliation already found these two disagree. Without passing that
        # through, the model sees two independent memories and lists both as if
        # they were separate appointments.
        contradicts = memory.get("contradicts") or []
        if contradicts:
            marker += "  [CONTRADICTS " + ", ".join(f"#{i}" for i in contradicts) + "]"
        lines.append(
            f"\n  #{memory['id']} ({memory['type']}){marker}"
            f"\n    {memory['content']}"
            f"\n    remembered from a dictation on {memory.get('timestamp', 'unknown date')}"
            f" in {memory.get('application') or 'an unknown app'}"
        )
        excerpt = memory.get("source_excerpt")
        if excerpt:
            lines.append(f'    source transcript #{memory.get("source_transcript_id")}: "{excerpt}"')

    lines += [
        "",
        "Answer the question using only the memory above.",
        "If it is not there, say you don't have it. If it disagrees with itself,",
        "say so and give both. List in used_memory_ids only the memories your",
        "answer actually draws on.",
    ]
    return "\n".join(lines)
