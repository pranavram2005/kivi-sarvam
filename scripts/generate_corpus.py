"""Generate the development corpus and the evaluation suite together.

    python scripts/generate_corpus.py

Writes:
    data/development_corpus.jsonl   ~500 transcript-like records for one user
    evaluation/cases.jsonl          evaluation cases grounded in that corpus

Why generate rather than hand-write
-----------------------------------
The two files must agree. An evaluation case that says "the answer should be
Friday at 4 PM" is only meaningful if the corpus really contains a 3 PM meeting
that was really moved to 4 PM, three dictations later, with nothing else
contradicting it. Emitting both from one description of the world is what keeps
them consistent, and re-running this script reproduces both exactly (fixed
seed).

The corpus is one person's work life over ten weeks: twelve colleagues, six
projects, and the ordinary mess of meetings being moved, numbers being
promised, preferences being stated once and expected to stick.

Distribution (the assignment suggests a split; this is the one used here):

    150  work / project conversations
    100  meetings
     70  people and relationship context
     50  preferences
     40  corrections and updates
     40  information spread across several transcripts
     30  irrelevant, nothing to remember
     20  ambiguous or conflicting
    ---
    500
"""

from __future__ import annotations

import json
import random
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_PATH = REPO_ROOT / "data" / "development_corpus.jsonl"
CASES_PATH = REPO_ROOT / "evaluation" / "cases.jsonl"

SEED = 20260831
START = datetime(2026, 6, 22, 9, 0)  # a Monday
END = datetime(2026, 8, 30, 18, 0)

APPS = ["Slack", "Notes", "Mail", "Messages", "Linear", "Reminders", "Cursor", "Terminal"]
WRITING_APPS = ["Slack", "Notes", "Mail", "Messages", "Linear"]


# ---------------------------------------------------------------------------
# The world
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Person:
    name: str
    role: str
    project: str
    team: str


@dataclass(frozen=True)
class Project:
    name: str
    topic: str
    second_topic: str


PEOPLE = [
    Person("Rahul", "engineering lead", "Project Atlas", "Platform"),
    Person("Sarah", "finance lead", "Project Atlas", "Finance"),
    Person("Priya", "design lead", "Project Beacon", "Design"),
    Person("Marcus", "engineering manager", "Project Cobalt", "Data"),
    Person("Aditi", "data scientist", "Project Cobalt", "Data"),
    Person("Tom", "account executive", "Project Forge", "Sales"),
    Person("Nadia", "customer success manager", "Project Beacon", "Success"),
    Person("Kenji", "infrastructure engineer", "Project Ember", "Platform"),
    Person("Elena", "contracts counsel", "Project Forge", "Legal"),
    Person("Dev", "QA lead", "Project Delta", "Quality"),
    Person("Lucia", "product marketer", "Project Delta", "Marketing"),
    Person("Omar", "recruiter", "Project Ember", "People"),
]

PROJECTS = [
    Project("Project Atlas", "enterprise pricing", "the renewal deck"),
    Project("Project Beacon", "the onboarding redesign", "activation metrics"),
    Project("Project Cobalt", "the data platform migration", "warehouse costs"),
    Project("Project Delta", "the mobile release", "crash reporting"),
    Project("Project Ember", "security hardening", "the SOC 2 audit"),
    Project("Project Forge", "partner integrations", "the API contract"),
]

# Each project gets a pool of topics rather than one. Without this, every
# meeting with the same person is about the same thing, and the memory system
# correctly - but uselessly - reads a calendar full of recurring meetings as one
# appointment being rescheduled over and over.
PROJECT_TOPICS: dict[str, list[str]] = {
    "Project Atlas": [
        "enterprise pricing", "the renewal deck", "the discount floor",
        "the Q4 forecast", "the customer escalation", "seat-based billing",
    ],
    "Project Beacon": [
        "the onboarding redesign", "activation metrics", "the empty-state screens",
        "the welcome email sequence", "the trial conversion drop", "the setup wizard",
    ],
    "Project Cobalt": [
        "the data platform migration", "warehouse costs", "the schema change",
        "the backfill plan", "query latency", "the reporting cutover",
    ],
    "Project Delta": [
        "the mobile release", "crash reporting", "the offline mode",
        "the app store review", "push notifications", "the tablet layout",
    ],
    "Project Ember": [
        "security hardening", "the SOC 2 audit", "the penetration test",
        "key rotation", "the access review", "incident runbooks",
    ],
    "Project Forge": [
        "partner integrations", "the API contract", "the sandbox environment",
        "rate limiting", "the webhook retries", "the partner onboarding guide",
    ],
}

BY_NAME = {p.name: p for p in PEOPLE}
PROJECT_BY_NAME = {p.name: p for p in PROJECTS}


# ---------------------------------------------------------------------------
# Raw ASR simulation
# ---------------------------------------------------------------------------
# Speech recognition mangles proper nouns and drops function words. The raw_asr
# field carries that damage so retrieval and extraction are exercised against
# realistic input rather than clean prose.
PHONETIC = {
    "rahul": "rahool",
    "priya": "prea",
    "aditi": "additi",
    "kenji": "kenjee",
    "nadia": "nadya",
    "elena": "alena",
    "lucia": "loosha",
    "atlas": "atlus",
    "beacon": "beecon",
    "cobalt": "cobolt",
    "ember": "amber",
    "forge": "forj",
    "pricing": "priceing",
    "renewal": "renuwal",
    "onboarding": "on boarding",
    "migration": "migraytion",
    "audit": "oddit",
    "invoice": "in voice",
    "quarterly": "quarterley",
    "roadmap": "road map",
    "deadline": "dead line",
}

DROPPABLE = {
    "the", "a", "an", "to", "of", "for", "on", "at", "in", "is", "are", "was",
    "and", "that", "with", "we", "i", "it", "be", "will", "my",
}


def to_raw_asr(text: str, rng: random.Random) -> str:
    """Degrade polished text into something a recogniser would plausibly emit."""
    words = re.findall(r"[A-Za-z0-9']+", text.lower())
    out: list[str] = []
    for word in words:
        if word in PHONETIC and rng.random() < 0.55:
            out.append(PHONETIC[word])
            continue
        if word in DROPPABLE and rng.random() < 0.45:
            continue
        if rng.random() < 0.03 and len(word) > 4:
            # a dropped or doubled consonant, the way a recogniser slips
            position = rng.randrange(1, len(word) - 1)
            out.append(word[:position] + word[position + 1 :])
            continue
        if rng.random() < 0.02:
            out.append(word)
        out.append(word)
    return " ".join(out)


# ---------------------------------------------------------------------------
# Record construction
# ---------------------------------------------------------------------------
@dataclass
class Corpus:
    rng: random.Random
    records: list[dict] = field(default_factory=list)
    cases: list[dict] = field(default_factory=list)
    _counter: int = 0

    @staticmethod
    def _tidy(text: str) -> str:
        """Collapse doubled articles and stray double spaces.

        Topics in this generator carry their own article ("the renewal deck"),
        so a template that writes one in front produces "the the". Catching it
        here means a new template can never reintroduce the glitch.
        """
        text = re.sub(
            r"\b(the|a|an)\s+(?:the|a|an)\b",
            lambda match: match.group(1),
            text,
            flags=re.IGNORECASE,
        )
        return re.sub(r"\s{2,}", " ", text).strip()

    def add(
        self,
        text: str,
        when: datetime,
        *,
        application: str | None = None,
        category: str = "work",
        workspace: str = "work",
        record_id: str | None = None,
    ) -> str:
        self._counter += 1
        identifier = record_id or f"tr_{self._counter:03d}"
        text = self._tidy(text)
        self.records.append(
            {
                "id": identifier,
                "timestamp": when.replace(second=0, microsecond=0).isoformat(),
                "application": application or self.rng.choice(WRITING_APPS),
                "raw_asr": to_raw_asr(text, self.rng),
                "formatted_output": text,
                "metadata": {"workspace": workspace, "category": category},
            }
        )
        return identifier

    def add_case(self, case: dict) -> None:
        self.cases.append(case)


def business_time(rng: random.Random, day: datetime) -> datetime:
    hour = rng.choice([9, 9, 10, 10, 11, 11, 13, 14, 14, 15, 16, 16, 17])
    minute = rng.choice([0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50])
    return day.replace(hour=hour, minute=minute)


def weekday_between(rng: random.Random, start: datetime, end: datetime) -> datetime:
    span = max(1, (end - start).days)
    for _ in range(12):
        day = start + timedelta(days=rng.randrange(span))
        if day.weekday() < 5:
            return business_time(rng, day)
    return business_time(rng, start)


# ---------------------------------------------------------------------------
# Anchored scenarios - the evaluation cases point at these
# ---------------------------------------------------------------------------
def build_anchors(corpus: Corpus) -> None:
    """Twelve scenarios with known-correct outcomes.

    Each one is placed at a fixed time so the evaluation can assert exactly what
    Kivi should end up believing.
    """
    rng = corpus.rng

    # --- 1. a plain durable fact ------------------------------------------
    corpus.add(
        "Rahul leads Project Atlas on the platform team.",
        datetime(2026, 6, 23, 9, 15),
        application="Notes",
        category="people",
        record_id="anchor_fact_rahul",
    )
    corpus.add_case(
        {
            "id": "eval_001",
            "category": "fact",
            "question": "Who leads Project Atlas?",
            "expected_behavior": "Name Rahul as the lead of Project Atlas.",
            "expect_answer_contains_all": ["Rahul"],
            "expect_abstain": False,
            "expect_sources_any": ["anchor_fact_rahul"],
        }
    )

    corpus.add(
        "Sarah is the finance lead and signs off on any pricing change.",
        datetime(2026, 6, 23, 11, 40),
        application="Notes",
        category="people",
        record_id="anchor_fact_sarah",
    )
    corpus.add_case(
        {
            "id": "eval_002",
            "category": "fact",
            "question": "Who is the finance lead?",
            "expected_behavior": "Name Sarah.",
            "expect_answer_contains_all": ["Sarah"],
            "expect_abstain": False,
            "expect_sources_any": ["anchor_fact_sarah"],
        }
    )

    # --- 2. information spread across three dictations ---------------------
    corpus.add(
        "Meeting with Rahul on Friday about Project Atlas pricing.",
        datetime(2026, 8, 24, 10, 30),
        application="Slack",
        category="meeting",
        record_id="anchor_multi_1",
    )
    corpus.add(
        "The Atlas pricing tiers still need to be agreed before we talk to the customer.",
        datetime(2026, 8, 24, 15, 5),
        application="Notes",
        category="work",
        record_id="anchor_multi_2",
    )
    corpus.add(
        "I need to send Rahul the revised numbers before the meeting.",
        datetime(2026, 8, 25, 13, 20),
        application="Notes",
        category="task",
        record_id="anchor_multi_3",
    )
    corpus.add_case(
        {
            "id": "eval_003",
            "category": "multi_transcript",
            "question": "What do I need to prepare for Rahul?",
            "expected_behavior": (
                "Combine several dictations: the Atlas pricing discussion and the "
                "revised numbers that were promised."
            ),
            "expect_answer_contains_any": ["revised numbers", "numbers"],
            "expect_abstain": False,
            "expect_min_sources": 2,
            "expect_sources_any": ["anchor_multi_3", "anchor_multi_1", "anchor_multi_2"],
        }
    )

    # --- 3. a correction ---------------------------------------------------
    corpus.add(
        "The Beacon empty-state walkthrough with Priya is on Thursday at 3 PM.",
        datetime(2026, 8, 18, 9, 45),
        application="Slack",
        category="meeting",
        record_id="anchor_correction_before",
    )
    corpus.add(
        "Actually, move the Beacon empty-state walkthrough with Priya to Thursday at 4 PM.",
        datetime(2026, 8, 20, 16, 20),
        application="Slack",
        category="correction",
        record_id="anchor_correction_after",
    )
    corpus.add_case(
        {
            "id": "eval_004",
            "category": "correction",
            "question": "When is the Beacon empty-state walkthrough with Priya?",
            "expected_behavior": "Answer 4 PM. The earlier 3 PM must have been superseded.",
            "expect_answer_contains_all": ["4"],
            "expect_answer_excludes": ["3 PM"],
            "expect_abstain": False,
            "expect_sources_any": ["anchor_correction_after"],
        }
    )
    corpus.add_case(
        {
            "id": "eval_005",
            "category": "memory_update",
            "question": "When is the Beacon empty-state walkthrough with Priya?",
            "expected_behavior": "The 3 PM memory should be SUPERSEDED, not ACTIVE.",
            "check_superseded_from": "anchor_correction_before",
            "expect_abstain": False,
        }
    )

    corpus.add(
        "The Cobalt migration deadline is the end of August.",
        datetime(2026, 7, 28, 10, 5),
        application="Linear",
        category="work",
        record_id="anchor_deadline_before",
    )
    corpus.add(
        "Correction: the Cobalt migration deadline moved to the end of September.",
        datetime(2026, 8, 11, 14, 30),
        application="Linear",
        category="correction",
        record_id="anchor_deadline_after",
    )
    corpus.add_case(
        {
            "id": "eval_006",
            "category": "correction",
            "question": "When is the Cobalt migration deadline?",
            "expected_behavior": "End of September, not end of August.",
            "expect_answer_contains_any": ["September"],
            "expect_answer_excludes": ["August"],
            "expect_abstain": False,
            "expect_sources_any": ["anchor_deadline_after"],
        }
    )

    # --- 4. preferences ----------------------------------------------------
    corpus.add(
        "Keep my client emails short and to the point.",
        datetime(2026, 6, 25, 11, 0),
        application="Mail",
        category="preference",
        record_id="anchor_pref_email",
    )
    corpus.add_case(
        {
            "id": "eval_007",
            "category": "preference",
            "question": "Draft a short message to Rahul about the meeting.",
            "expected_behavior": (
                "Produce a brief draft and cite the stored preference about keeping "
                "client emails short."
            ),
            "expect_abstain": False,
            "expect_sources_any": ["anchor_pref_email"],
            "expect_answer_max_words": 130,
        }
    )
    corpus.add(
        "I prefer meeting summaries in bullet points, not paragraphs.",
        datetime(2026, 6, 26, 16, 30),
        application="Notes",
        category="preference",
        record_id="anchor_pref_summary",
    )
    corpus.add_case(
        {
            "id": "eval_008",
            "category": "preference",
            "question": "How do I prefer my meeting summaries?",
            "expected_behavior": "Bullet points.",
            "expect_answer_contains_any": ["bullet"],
            "expect_abstain": False,
            "expect_sources_any": ["anchor_pref_summary"],
        }
    )

    # --- 5. nothing to remember -------------------------------------------
    for index, text in enumerate(
        [
            "Hmm okay, give me a second.",
            "Um, let me think about that for a moment.",
            "Testing one two three, can you hear me.",
        ],
        start=1,
    ):
        identifier = corpus.add(
            text,
            datetime(2026, 7, 6 + index, 12, 0),
            application="Notes",
            category="irrelevant",
            record_id=f"anchor_filler_{index}",
        )
        corpus.add_case(
            {
                "id": f"eval_0{8 + index}",
                "category": "irrelevant",
                "question": None,
                "expected_behavior": "No durable memory should be created from this dictation.",
                "check_no_memory_from": identifier,
            }
        )

    # --- 6. abstention -----------------------------------------------------
    corpus.add_case(
        {
            "id": "eval_012",
            "category": "abstention",
            "question": "When is Rahul's birthday?",
            "expected_behavior": "Say the information is not in the user's history. Do not guess.",
            "expect_abstain": True,
            "expect_answer_contains_any": ["don't have", "do not have", "nothing about"],
        }
    )
    corpus.add_case(
        {
            "id": "eval_013",
            "category": "abstention",
            "question": "What is Sarah's phone number?",
            "expected_behavior": "Abstain - no phone number was ever dictated.",
            "expect_abstain": True,
        }
    )
    corpus.add_case(
        {
            "id": "eval_014",
            "category": "abstention",
            "question": "Which university did Marcus go to?",
            "expected_behavior": "Abstain - nothing in the history covers this.",
            "expect_abstain": True,
        }
    )

    # --- 7. a genuine conflict, with no correction to resolve it ----------
    corpus.add(
        "Meeting with Tom is on Monday at 10 AM about the Forge partner contract.",
        datetime(2026, 8, 3, 9, 30),
        application="Slack",
        category="conflict",
        record_id="anchor_conflict_a",
    )
    corpus.add(
        "Meeting with Tom is on Monday at 10 AM about the Forge partner contract.",
        datetime(2026, 8, 3, 9, 31),
        application="Notes",
        category="conflict",
        record_id="anchor_conflict_dup",
    )
    corpus.add(
        "Tom said the Forge partner contract meeting is on Tuesday at 2 PM.",
        datetime(2026, 8, 3, 9, 32),
        application="Messages",
        category="conflict",
        record_id="anchor_conflict_b",
    )
    corpus.add_case(
        {
            "id": "eval_015",
            "category": "conflict",
            "question": "When is my meeting with Tom about the Forge partner contract?",
            "expected_behavior": (
                "Two different times exist and nothing marks either as current. Say so "
                "rather than picking one."
            ),
            "expect_conflict": True,
            "expect_abstain": False,
        }
    )
    corpus.add_case(
        {
            "id": "eval_016",
            "category": "duplicate",
            "question": None,
            "expected_behavior": (
                "The word-for-word repeat should not create a second identical memory."
            ),
            "check_duplicate_from": "anchor_conflict_dup",
        }
    )

    # --- 8. provenance -----------------------------------------------------
    corpus.add_case(
        {
            "id": "eval_017",
            "category": "provenance",
            "question": "Why do you think Rahul wants the revised numbers?",
            "expected_behavior": "Point at the memory and the transcript it came from.",
            "expect_abstain": False,
            "expect_min_sources": 1,
            "expect_sources_any": ["anchor_multi_3"],
        }
    )

    # --- 9. retrieval over a specific topic --------------------------------
    corpus.add(
        "Nadia raised that Beacon activation dropped 12 percent after the onboarding change.",
        datetime(2026, 8, 12, 14, 15),
        application="Slack",
        category="work",
        record_id="anchor_topic_activation",
    )
    corpus.add_case(
        {
            "id": "eval_018",
            "category": "retrieval",
            "question": "What did Nadia say about Beacon activation?",
            "expected_behavior": "Recall the 12 percent activation drop.",
            "expect_answer_contains_any": ["12", "activation"],
            "expect_abstain": False,
            "expect_sources_any": ["anchor_topic_activation"],
        }
    )

    corpus.add(
        "Elena flagged that the Forge partner contract needs an indemnity clause before signature.",
        datetime(2026, 8, 14, 11, 25),
        application="Mail",
        category="work",
        record_id="anchor_topic_indemnity",
    )
    corpus.add_case(
        {
            "id": "eval_019",
            "category": "retrieval",
            "question": "What did Elena flag about the Forge contract?",
            "expected_behavior": "The indemnity clause needed before signature.",
            "expect_answer_contains_any": ["indemnity"],
            "expect_abstain": False,
            "expect_sources_any": ["anchor_topic_indemnity"],
        }
    )

    # --- 10. commitments ---------------------------------------------------
    corpus.add(
        "I promised Kenji I would review the Ember security checklist before Wednesday.",
        datetime(2026, 8, 19, 17, 10),
        application="Slack",
        category="task",
        record_id="anchor_task_kenji",
    )
    corpus.add_case(
        {
            "id": "eval_020",
            "category": "multi_transcript",
            "question": "What do I need to prepare for Kenji?",
            "expected_behavior": "The Ember security checklist review.",
            "expect_answer_contains_any": ["checklist", "Ember", "security"],
            "expect_abstain": False,
            "expect_sources_any": ["anchor_task_kenji"],
        }
    )

    # --- 11. a person Kivi has never heard of -----------------------------
    corpus.add_case(
        {
            "id": "eval_021",
            "category": "abstention",
            "question": "What did Gregor say about the roadmap?",
            "expected_behavior": "Kivi has never heard of Gregor. Abstain.",
            "expect_abstain": True,
        }
    )

    # --- 12. a preference that was later changed --------------------------
    corpus.add(
        "Investor updates should be detailed and thorough.",
        datetime(2026, 7, 2, 10, 0),
        application="Notes",
        category="preference",
        record_id="anchor_pref_investor_before",
    )
    corpus.add(
        "Actually, investor updates should be short from now on, one page at most.",
        datetime(2026, 8, 6, 15, 45),
        application="Notes",
        category="correction",
        record_id="anchor_pref_investor_after",
    )
    corpus.add_case(
        {
            "id": "eval_022",
            "category": "correction",
            "question": "How should investor updates be written?",
            "expected_behavior": "Short - the earlier 'detailed' preference was superseded.",
            "expect_answer_contains_any": ["short", "one page"],
            "expect_abstain": False,
            "expect_sources_any": ["anchor_pref_investor_after"],
        }
    )


def build_anchors_extra(corpus: Corpus) -> None:
    """A second batch of anchored scenarios, widening coverage across the world.

    Same idea as `build_anchors`, spread over more people and projects so the
    suite is not measuring one storyline eight different ways.
    """

    # --- facts -------------------------------------------------------------
    facts = [
        ("Marcus is the engineering manager for Project Cobalt.", "Marcus", "Who manages Project Cobalt?", "Marcus"),
        ("Dev runs QA for the Delta mobile release.", "Dev", "Who runs QA for the mobile release?", "Dev"),
        ("Elena is our contracts counsel and reviews every partner agreement.", "Elena", "Who reviews partner agreements?", "Elena"),
        ("Kenji owns the infrastructure work on Project Ember.", "Kenji", "Who owns the infrastructure work on Project Ember?", "Kenji"),
    ]
    for index, (text, _who, question, expected) in enumerate(facts, start=1):
        identifier = f"anchor_x_fact_{index}"
        corpus.add(
            text,
            datetime(2026, 6, 24, 9, 20) + timedelta(hours=index),
            application="Notes",
            category="people",
            record_id=identifier,
        )
        corpus.add_case(
            {
                "id": f"eval_1{index:02d}",
                "category": "fact",
                "question": question,
                "expected_behavior": f"Name {expected}.",
                "expect_answer_contains_all": [expected],
                "expect_abstain": False,
                "expect_sources_any": [identifier],
            }
        )

    # --- retrieval over specific topics -----------------------------------
    topics = [
        (
            "Aditi found that warehouse costs on Cobalt tripled after the schema change.",
            "What did Aditi find about warehouse costs?",
            ["tripled", "warehouse"],
        ),
        (
            "Lucia wants the Delta launch messaging to lead with reliability, not speed.",
            "What does Lucia want the Delta launch messaging to lead with?",
            ["reliability"],
        ),
        (
            "Omar said the security engineer role for Ember has two candidates at final stage.",
            "What did Omar say about the security engineer role?",
            ["candidates", "final"],
        ),
        (
            "Marcus estimated the Cobalt migration needs three more engineering weeks.",
            "How much longer does the Cobalt migration need?",
            ["three", "weeks"],
        ),
        (
            "Sarah rejected the Atlas discount floor because it breaks the margin target.",
            "Why did Sarah reject the Atlas discount floor?",
            ["margin"],
        ),
    ]
    for index, (text, question, expected) in enumerate(topics, start=1):
        identifier = f"anchor_x_topic_{index}"
        corpus.add(
            text,
            datetime(2026, 8, 4, 11, 30) + timedelta(days=index),
            application="Slack",
            category="work",
            record_id=identifier,
        )
        corpus.add_case(
            {
                "id": f"eval_2{index:02d}",
                "category": "retrieval",
                "question": question,
                "expected_behavior": f"Recall the detail: {', '.join(expected)}.",
                "expect_answer_contains_any": expected,
                "expect_abstain": False,
                "expect_sources_any": [identifier],
            }
        )

    # --- corrections -------------------------------------------------------
    corrections = [
        (
            "Meeting with Marcus is on Tuesday at 11 AM to review the Cobalt plan.",
            "Actually, move the Marcus Cobalt plan review to Wednesday at 11 AM.",
            "When is my meeting with Marcus?",
            ["Wednesday"],
            ["Tuesday"],
        ),
        (
            "The Delta release ships on the 14th.",
            "Correction: the Delta release now ships on the 21st.",
            "When does the Delta release ship?",
            ["21"],
            ["14th"],
        ),
        (
            "Meeting with Aditi is on Monday at 9 AM about the Cobalt schema.",
            "Push the Aditi Cobalt schema check-in to Thursday at 9 AM.",
            "When is my meeting with Aditi?",
            ["Thursday"],
            ["Monday"],
        ),
    ]
    for index, (before, after, question, expected, excluded) in enumerate(corrections, start=1):
        before_id = f"anchor_x_corr_{index}_before"
        after_id = f"anchor_x_corr_{index}_after"
        corpus.add(
            before,
            datetime(2026, 8, 9, 9, 30) + timedelta(days=index),
            application="Slack",
            category="meeting",
            record_id=before_id,
        )
        corpus.add(
            after,
            datetime(2026, 8, 12, 16, 0) + timedelta(days=index),
            application="Slack",
            category="correction",
            record_id=after_id,
        )
        corpus.add_case(
            {
                "id": f"eval_3{index:02d}",
                "category": "correction",
                "question": question,
                "expected_behavior": f"Answer {expected[0]}; the earlier value was superseded.",
                "expect_answer_contains_any": expected,
                "expect_answer_excludes": excluded,
                "expect_abstain": False,
                "expect_sources_any": [after_id],
                "check_superseded_from": before_id,
            }
        )

    # --- conflicts ---------------------------------------------------------
    conflicts = [
        ("Priya", "Wednesday at 2 PM", "Thursday at 11 AM", "the Beacon design review"),
        ("Sarah", "Friday at 9 AM", "Monday at 4 PM", "the Atlas pricing sign-off"),
    ]
    for index, (who, first, second, what) in enumerate(conflicts, start=1):
        day = datetime(2026, 8, 20, 10, 0) + timedelta(days=index)
        corpus.add(
            f"{who} has {what} down for {first}.",
            day,
            application="Messages",
            category="ambiguous",
            record_id=f"anchor_x_conflict_{index}_a",
        )
        corpus.add(
            f"I have {what} with {who} as {second}.",
            day + timedelta(minutes=3),
            application="Notes",
            category="ambiguous",
            record_id=f"anchor_x_conflict_{index}_b",
        )
        corpus.add_case(
            {
                "id": f"eval_4{index:02d}",
                "category": "conflict",
                "question": f"When is {what} with {who}?",
                "expected_behavior": "Two times exist with no correction between them; say so.",
                "expect_conflict": True,
                "expect_abstain": False,
            }
        )

    # --- preferences -------------------------------------------------------
    preferences = [
        (
            "Keep my Slack messages to two sentences at most.",
            "How long should my Slack messages be?",
            ["two sentences", "two"],
        ),
        (
            "Use formal language for anything going to the board.",
            "What tone should I use for board communication?",
            ["formal"],
        ),
        (
            "I prefer status updates to open with the decision, not the background.",
            "How do I prefer status updates to open?",
            ["decision"],
        ),
    ]
    for index, (text, question, expected) in enumerate(preferences, start=1):
        identifier = f"anchor_x_pref_{index}"
        corpus.add(
            text,
            datetime(2026, 6, 26, 10, 15) + timedelta(days=index),
            application="Notes",
            category="preference",
            record_id=identifier,
        )
        corpus.add_case(
            {
                "id": f"eval_5{index:02d}",
                "category": "preference",
                "question": question,
                "expected_behavior": f"Recall the stored preference: {expected[0]}.",
                "expect_answer_contains_any": expected,
                "expect_abstain": False,
                "expect_sources_any": [identifier],
            }
        )

    # --- abstention --------------------------------------------------------
    abstentions = [
        "What is Priya's home address?",
        "How much does Kenji earn?",
        "What did Marcus think of the football match?",
        "When did Elena join the company?",
        "What is Tom's dietary requirement?",
    ]
    for index, question in enumerate(abstentions, start=1):
        corpus.add_case(
            {
                "id": f"eval_6{index:02d}",
                "category": "abstention",
                "question": question,
                "expected_behavior": "Nothing in the history covers this. Abstain rather than guess.",
                "expect_abstain": True,
            }
        )

    # --- nothing to remember ----------------------------------------------
    fillers = [
        "Right, where was I.",
        "Sorry, what was that.",
        "Never mind, ignore that.",
        "Uh, hold on a moment.",
    ]
    for index, text in enumerate(fillers, start=1):
        identifier = f"anchor_x_filler_{index}"
        corpus.add(
            text,
            datetime(2026, 7, 14, 12, 30) + timedelta(days=index),
            application="Notes",
            category="irrelevant",
            record_id=identifier,
        )
        corpus.add_case(
            {
                "id": f"eval_7{index:02d}",
                "category": "irrelevant",
                "question": None,
                "expected_behavior": "No durable memory should be created.",
                "check_no_memory_from": identifier,
            }
        )

    # --- multi-transcript threads with a known answer ---------------------
    threads = [
        (
            "Priya",
            "Project Beacon",
            [
                "Meeting with Priya on Thursday about the Beacon onboarding flow.",
                "The Beacon onboarding flow still needs the empty-state screens.",
                "I need to send Priya the empty-state copy before Thursday.",
            ],
            "What do I need to prepare for Priya?",
            ["empty-state", "empty"],
        ),
        (
            "Sarah",
            "Project Atlas",
            [
                "Meeting with Sarah on Monday about the Atlas renewal deck.",
                "The Atlas renewal deck is missing the churn assumptions.",
                "I owe Sarah the churn assumptions before Monday.",
            ],
            "What do I owe Sarah?",
            ["churn"],
        ),
    ]
    for index, (who, _project, lines, question, expected) in enumerate(threads, start=1):
        base = datetime(2026, 8, 24, 9, 0) + timedelta(days=index)
        ids = []
        for step, line in enumerate(lines):
            identifier = f"anchor_x_thread_{index}_{step}"
            ids.append(identifier)
            corpus.add(
                line,
                base + timedelta(days=step, hours=step),
                application=["Slack", "Notes", "Notes"][step],
                category="multi",
                record_id=identifier,
            )
        corpus.add_case(
            {
                "id": f"eval_8{index:02d}",
                "category": "multi_transcript",
                "question": question,
                "expected_behavior": f"Combine the thread about {who} into one answer.",
                "expect_answer_contains_any": expected,
                "expect_abstain": False,
                "expect_min_sources": 1,
                "expect_sources_any": ids,
            }
        )

    # --- provenance --------------------------------------------------------
    corpus.add_case(
        {
            "id": "eval_901",
            "category": "provenance",
            "question": "Why do you think Sarah rejected the Atlas discount floor?",
            "expected_behavior": "Cite the memory and the transcript it came from.",
            "expect_abstain": False,
            "expect_min_sources": 1,
            "expect_sources_any": ["anchor_x_topic_5"],
        }
    )
    corpus.add_case(
        {
            "id": "eval_902",
            "category": "provenance",
            "question": "Why do you think the Cobalt warehouse costs tripled?",
            "expected_behavior": "Cite the memory and the transcript it came from.",
            "expect_abstain": False,
            "expect_min_sources": 1,
            "expect_sources_any": ["anchor_x_topic_1"],
        }
    )


# ---------------------------------------------------------------------------
# Bulk generation
# ---------------------------------------------------------------------------
WORK_TEMPLATES = [
    "{person} raised a question about {topic} on {project}.",
    "We went through {topic} with {person} and the numbers still do not line up.",
    "{person} thinks {topic} is the main risk on {project} this quarter.",
    "{project} is blocked on {topic} until {person} comes back with a decision.",
    "Talked to {person} about {topic}; they want a written summary before the review.",
    "{person} pushed back on {topic} and suggested we simplify it.",
    "{topic_cap} work on {project} is roughly two weeks behind where I expected.",
    "{person} shared new data on {topic} and it changes how we should frame {project}.",
    "We agreed with {person} that {topic} comes before anything else on {project}.",
    "{person} is worried {second_topic} will slip if {project} is not staffed properly.",
    "Spent the morning on {second_topic} for {project}; it is messier than it looked.",
    "{topic_cap} is the one thing on {project} nobody has picked up.",
    "{person} asked whether {topic} affects {second_topic}.",
    "{topic_cap} decision on {project} needs {person} in the room to be final.",
    "{person} wants a one-page summary of where {project} stands on {topic}.",
    "Reviewed {second_topic} with {person}; two open items left on {project}.",
]

MEETING_TEMPLATES = [
    "Meeting with {person} on {day} at {time} about {topic}.",
    "{person} sync on {day} at {time} to go through {project}.",
    "Booked a {day} {time} review with {person} about {second_topic}.",
    "The {project} standup with {person} moves to {day} at {time} this week.",
    "Catch-up with {person} on {day} at {time} about {topic}.",
    "Design review for {project} with {person} is {day} at {time}.",
    "{person} wants a call on {day} at {time} to close out {topic}.",
]

PEOPLE_TEMPLATES = [
    "{person} is the {role} on {project}.",
    "{person} sits on the {team} team and works on {project}.",
    "{person} is the person to ask about {topic} on {project}.",
    "{person} is the person to ask about {topic}.",
    "{person} joined the {team} team and is picking up {project}.",
    "{person} works on {project} day to day.",
    "{person} is our {role}; {other} covers for them when they are away.",
]

PREFERENCE_TEMPLATES = [
    "Keep my {doc_type} {style}.",
    "I prefer {doc_type} written in {style2}.",
    "Always use {tone} language for {audience} communication.",
    "My {doc_type} should never run longer than {length}.",
    "I like {doc_type} to open with the decision, not the background.",
    "Use {tone} tone when I am writing to {audience}.",
    "I prefer {doc_type} in bullet points rather than prose.",
]

CORRECTION_TEMPLATES = [
    "Actually, move the {person} meeting to {day} at {time}.",
    "Correction: the {project} deadline is now {day}, not what I said earlier.",
    "Scratch that, {person} is on the {team} team, not where I said.",
    "Change the {person} sync to {day} at {time} instead.",
    "Update: {topic} on {project} is no longer blocked.",
    "I meant {day} at {time} for the {person} review, not the time I gave before.",
    "Push the {project} check-in with {person} to {day} at {time}.",
]

IRRELEVANT_TEXTS = [
    "Hmm, okay.",
    "Give me a second.",
    "Um, let me see.",
    "Right, where was I.",
    "Uh, hold on.",
    "Okay.",
    "Let me think.",
    "Sorry, what was that.",
    "One sec.",
    "Never mind.",
    "Can you hear me.",
    "Testing one two.",
    "Yeah.",
    "Hmm, no.",
    "Alright.",
    "Ah, okay then.",
    "Erm.",
    "Is this thing on.",
    "Delete that.",
    "Ignore that.",
]

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
TIMES = ["9 AM", "10 AM", "11 AM", "1 PM", "2 PM", "3 PM", "4 PM", "4:30 PM", "5 PM"]
DOC_TYPES = ["client emails", "meeting summaries", "status updates", "internal notes", "release notes"]
STYLES = ["short and to the point", "brief", "concise", "tight", "under five sentences"]
STYLES2 = ["a plain, direct style", "short paragraphs", "bullet points", "a formal register"]
TONES = ["professional", "formal", "warm but direct", "plain", "neutral"]
AUDIENCES = ["investor", "customer", "partner", "board", "executive"]
LENGTHS = ["one page", "five bullets", "three paragraphs", "200 words"]


# One person holds one preference per kind of document. Drawing style at random
# per record produced a corpus where the user wanted their summaries "short",
# "tight" and "brief" in the same week - which the memory system correctly, but
# uselessly, reported as a contradiction. Deliberate preference *changes* are
# handled by the anchored correction scenarios instead.
STABLE_PREFERENCES: dict[str, dict[str, str]] = {
    "client emails": {"style": "short and to the point", "style2": "short paragraphs", "tone": "warm but direct", "length": "one page"},
    "meeting summaries": {"style": "brief", "style2": "bullet points", "tone": "plain", "length": "five bullets"},
    "status updates": {"style": "tight", "style2": "bullet points", "tone": "neutral", "length": "three paragraphs"},
    "internal notes": {"style": "brief", "style2": "a plain, direct style", "tone": "plain", "length": "200 words"},
    "release notes": {"style": "concise", "style2": "bullet points", "tone": "professional", "length": "one page"},
}

# The audience a given tone belongs to, also fixed, for the same reason.
STABLE_TONES: dict[str, str] = {
    "investor": "professional",
    "customer": "warm but direct",
    "partner": "professional",
    "board": "formal",
    "executive": "neutral",
}


def fill(template: str, rng: random.Random) -> str:
    person = rng.choice(PEOPLE)
    other = rng.choice([p for p in PEOPLE if p.name != person.name])
    # Usually the project this person owns, occasionally one they only touch.
    project = (
        PROJECT_BY_NAME.get(person.project, PROJECTS[0])
        if rng.random() < 0.75
        else rng.choice(PROJECTS)
    )
    topics = PROJECT_TOPICS.get(project.name, [project.topic, project.second_topic])
    topic, second_topic = rng.sample(topics, 2)
    doc_type = rng.choice(DOC_TYPES)
    preference = STABLE_PREFERENCES[doc_type]
    audience = rng.choice(AUDIENCES)
    return template.format(
        person=person.name,
        other=other.name,
        role=person.role,
        team=person.team,
        project=project.name,
        topic=topic,
        second_topic=second_topic,
        # Topics already carry their article ("the renewal deck"), so a template
        # that needs one at the start of a sentence uses this capitalised form
        # rather than prefixing another "The".
        topic_cap=topic[:1].upper() + topic[1:],
        day=rng.choice(DAYS),
        time=rng.choice(TIMES),
        doc_type=doc_type,
        style=preference["style"],
        style2=preference["style2"],
        tone=STABLE_TONES.get(audience, preference["tone"]),
        audience=audience,
        length=preference["length"],
    )


def build_bulk(corpus: Corpus, counts: dict[str, int]) -> None:
    rng = corpus.rng

    def emit(template_pool: list[str], count: int, category: str, app_pool: list[str]) -> None:
        for _ in range(count):
            corpus.add(
                fill(rng.choice(template_pool), rng),
                weekday_between(rng, START, END),
                application=rng.choice(app_pool),
                category=category,
            )

    emit(WORK_TEMPLATES, counts["work"], "work", WRITING_APPS)
    emit(MEETING_TEMPLATES, counts["meeting"], "meeting", ["Slack", "Notes", "Reminders", "Linear"])
    emit(PEOPLE_TEMPLATES, counts["people"], "people", ["Notes", "Slack"])
    emit(PREFERENCE_TEMPLATES, counts["preference"], "preference", ["Notes", "Mail"])
    emit(CORRECTION_TEMPLATES, counts["correction"], "correction", ["Slack", "Notes", "Messages"])

    # Threads: one situation told across three dictations on consecutive days,
    # which is what multi-transcript reasoning is actually tested against.
    for _ in range(counts["multi"] // 3):
        person = rng.choice(PEOPLE)
        project = PROJECT_BY_NAME.get(person.project, rng.choice(PROJECTS))
        day = weekday_between(rng, START, END - timedelta(days=4))
        corpus.add(
            f"Meeting with {person.name} on {rng.choice(DAYS)} about {project.topic}.",
            day,
            application="Slack",
            category="multi",
        )
        corpus.add(
            f"{project.name} still has an open question on {project.second_topic}.",
            business_time(rng, day + timedelta(days=1)),
            application="Notes",
            category="multi",
        )
        corpus.add(
            f"I need to send {person.name} the updated figures before we meet.",
            business_time(rng, day + timedelta(days=2)),
            application="Notes",
            category="multi",
        )

    for index in range(counts["irrelevant"]):
        corpus.add(
            IRRELEVANT_TEXTS[index % len(IRRELEVANT_TEXTS)],
            weekday_between(rng, START, END),
            application=rng.choice(["Notes", "Messages", "Slack"]),
            category="irrelevant",
        )

    # Ambiguous pairs: two times for the same thing, dictated minutes apart so
    # neither reads as a later correction of the other.
    for _ in range(counts["ambiguous"] // 2):
        person = rng.choice(PEOPLE)
        day = weekday_between(rng, START, END)
        first, second = rng.sample(DAYS, 2)
        corpus.add(
            f"{person.name} said the review is on {first} at {rng.choice(TIMES)}.",
            day,
            application="Messages",
            category="ambiguous",
        )
        corpus.add(
            f"I have the {person.name} review down as {second} at {rng.choice(TIMES)}.",
            day + timedelta(minutes=2),
            application="Notes",
            category="ambiguous",
        )


# ---------------------------------------------------------------------------
def main() -> int:
    rng = random.Random(SEED)
    corpus = Corpus(rng=rng)

    build_anchors(corpus)
    build_anchors_extra(corpus)
    anchored = len(corpus.records)

    # The remaining budget, so the total lands on 500 regardless of how many
    # records the anchored scenarios needed.
    target = 500
    remaining = target - anchored
    plan = {
        "work": 150,
        "meeting": 100,
        "people": 70,
        "preference": 50,
        "correction": 40,
        "multi": 39,  # multiple of 3
        "irrelevant": 30,
        "ambiguous": 20,
    }
    scale = remaining / sum(plan.values())
    counts = {key: max(1, round(value * scale)) for key, value in plan.items()}
    counts["multi"] = (counts["multi"] // 3) * 3

    build_bulk(corpus, counts)

    # Trim or top up to land exactly on 500.
    while len(corpus.records) > target:
        for index in range(len(corpus.records) - 1, -1, -1):
            if corpus.records[index]["metadata"]["category"] == "work":
                corpus.records.pop(index)
                break
        else:
            corpus.records.pop()
    while len(corpus.records) < target:
        corpus.add(
            fill(rng.choice(WORK_TEMPLATES), rng),
            weekday_between(rng, START, END),
            category="work",
        )

    corpus.records.sort(key=lambda r: (r["timestamp"], r["id"]))

    CORPUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CASES_PATH.parent.mkdir(parents=True, exist_ok=True)

    with CORPUS_PATH.open("w", encoding="utf-8", newline="\n") as handle:
        for record in corpus.records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    with CASES_PATH.open("w", encoding="utf-8", newline="\n") as handle:
        for case in corpus.cases:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")

    distribution: dict[str, int] = {}
    for record in corpus.records:
        key = record["metadata"]["category"]
        distribution[key] = distribution.get(key, 0) + 1

    print(f"corpus : {len(corpus.records)} records -> {CORPUS_PATH.relative_to(REPO_ROOT)}")
    for key in sorted(distribution):
        print(f"           {distribution[key]:4d}  {key}")
    print(f"cases  : {len(corpus.cases)} evaluation cases -> {CASES_PATH.relative_to(REPO_ROOT)}")
    categories: dict[str, int] = {}
    for case in corpus.cases:
        categories[case["category"]] = categories.get(case["category"], 0) + 1
    for key in sorted(categories):
        print(f"           {categories[key]:4d}  {key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
