"""Shared text utilities: tokenisation, stopwords, normalisation.

Kept dependency-free and deterministic so that retrieval behaves identically on
every machine and in every evaluation run.
"""

from __future__ import annotations

import re
import unicodedata

# Words that carry no retrieval signal. Deliberately small - an over-eager
# stopword list destroys short queries like "when is my meeting".
STOPWORDS: frozenset[str] = frozenset(
    """
    a an the and or but if then than that this these those there here
    is am are was were be been being do does did doing done
    have has had having will would shall should can could may might must
    i me my mine myself you your yours we us our ours they them their
    he him his she her hers it its
    of to in on at by for with from into about as up down out over under
    again very just also too so such only own same
    what which who whom whose when where why how
    """.split()
)

# Words that indicate the user is asking about time - used to boost recency
# and event-typed memories.
TIME_WORDS: frozenset[str] = frozenset(
    """
    when time date day today tomorrow yesterday tonight morning afternoon
    evening now soon upcoming next last week weekend month schedule
    scheduled reschedule moved postponed monday tuesday wednesday thursday
    friday saturday sunday am pm oclock
    """.split()
)

# Words that indicate the user is asking about how they like things done.
PREFERENCE_WORDS: frozenset[str] = frozenset(
    """
    prefer prefers preference style tone usual usually normally always
    format formatting draft write writing voice concise short brief long
    detailed bullet bullets formal informal professional casual
    """.split()
)

# Words that indicate the user is asking about commitments / to-dos.
TASK_WORDS: frozenset[str] = frozenset(
    """
    prepare prep task todo owe owed send sending share deliver deliverable
    follow followup action commit committed promised need needs must
    before deadline due outstanding pending
    """.split()
)

_WORD_RE = re.compile(r"[a-z0-9]+")


def normalise(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().lower()


def tokenize(text: str, drop_stopwords: bool = True) -> list[str]:
    """Split text into lowercase alphanumeric tokens."""
    tokens = _WORD_RE.findall(normalise(text))
    if drop_stopwords:
        tokens = [t for t in tokens if t not in STOPWORDS]
    return tokens


def content_tokens(text: str) -> list[str]:
    """Tokens worth indexing: stopwords removed, single letters removed."""
    return [t for t in tokenize(text) if len(t) > 1 or t.isdigit()]


def bigrams(tokens: list[str]) -> list[str]:
    return [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]


def char_ngrams(token: str, n: int = 4) -> list[str]:
    """Character n-grams of a padded token.

    These make retrieval robust to the kind of damage speech recognition does
    to a word ("atlus" still overlaps heavily with "atlas").
    """
    if len(token) < 3:
        return []
    padded = f"#{token}#"
    if len(padded) <= n:
        return [padded]
    return [padded[i : i + n] for i in range(len(padded) - n + 1)]


def has_any(tokens: list[str], vocabulary: frozenset[str]) -> bool:
    return any(t in vocabulary for t in tokens)


def overlap_count(tokens: list[str], vocabulary: frozenset[str]) -> int:
    return sum(1 for t in tokens if t in vocabulary)


# ---------------------------------------------------------------------------
# Rough token accounting for cost reporting when a provider returns no usage.
# ---------------------------------------------------------------------------
def estimate_tokens(text: str) -> int:
    """~4 characters per token, the usual English approximation."""
    if not text:
        return 0
    return max(1, round(len(text) / 4))
