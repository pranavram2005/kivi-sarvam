"""Run the evaluation suite against the live pipeline.

    python evaluation/run_eval.py
    python evaluation/run_eval.py --cases evaluation/cases.jsonl
    python evaluation/run_eval.py --category abstention
    python evaluation/run_eval.py --verbose

What this measures
------------------
Every case runs against the real system: real stored memory, real retrieval,
real model decisions. Nothing is stubbed. The suite covers the eight behaviours
that decide whether this product can be trusted:

    fact              a single durable fact is recalled correctly
    retrieval         a specific detail is found among ~500 dictations
    multi_transcript  several dictations are combined into one answer
    correction        a superseded value is not used
    memory_update     the superseded memory really is marked SUPERSEDED
    duplicate         a word-for-word repeat does not become a second memory
    preference        a stored preference shapes a draft
    irrelevant        filler produces no durable memory
    abstention        a question with no supporting memory is refused
    conflict          two live values are surfaced, not silently resolved
    provenance        the answer names the memory and transcript behind it

Failures are printed in full and written to the results file. They are not
hidden: a suite that only reports its successes is not an evaluation.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# A model can put a non-breaking hyphen, curly quote or dash in an answer, and
# the default Windows console encoding (cp1252) cannot encode those - printing
# one raises UnicodeEncodeError and kills the run. Reviewers run this on
# Windows, so make stdout UTF-8 and never let a character break a suite.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # already wrapped, or not a TTY
        pass

from backend.config import REPO_ROOT, get_settings  # noqa: E402
from backend.database.db import init_db  # noqa: E402
from backend.llm.embeddings import get_embedder  # noqa: E402
from backend.llm.engine import get_engine  # noqa: E402
from backend.memory import store  # noqa: E402
from backend.memory.heykivi import ask  # noqa: E402

CASES_PATH = REPO_ROOT / "evaluation" / "cases.jsonl"
RESULTS_DIR = REPO_ROOT / "evaluation" / "results"

# Categories whose cases inspect stored state rather than asking a question.
STATE_CHECKS = ("check_no_memory_from", "check_superseded_from", "check_duplicate_from")


@dataclass
class CaseResult:
    case_id: str
    category: str
    question: str | None
    expected_behavior: str
    passed: bool = False
    failures: list[str] = field(default_factory=list)
    answer: str = ""
    abstained: bool = False
    conflict: bool = False
    supported: bool = False
    confidence: float = 0.0
    reasoning: str = ""
    retrieved_memory_ids: list[int] = field(default_factory=list)
    used_memory_ids: list[int] = field(default_factory=list)
    retrieved_sources: list[str] = field(default_factory=list)
    used_sources: list[str] = field(default_factory=list)
    expected_sources: list[str] = field(default_factory=list)
    source_retrieved: bool | None = None
    source_used: bool | None = None
    retrieval_latency_ms: float = 0.0
    llm_latency_ms: float = 0.0
    end_to_end_latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        data = {
            "test_id": self.case_id,
            "category": self.category,
            "question": self.question,
            "expected_behavior": self.expected_behavior,
            "passed": self.passed,
            "failures": self.failures,
            "answer": self.answer,
            "abstained": self.abstained,
            "conflict": self.conflict,
            "supported": self.supported,
            "confidence": round(self.confidence, 3),
            "reasoning": self.reasoning,
            "retrieved_memory_ids": self.retrieved_memory_ids,
            "used_memory_ids": self.used_memory_ids,
            "retrieved_source_transcripts": self.retrieved_sources,
            "used_source_transcripts": self.used_sources,
            "expected_source_transcripts": self.expected_sources,
            "expected_source_retrieved": self.source_retrieved,
            "expected_source_used": self.source_used,
            "retrieval_latency_ms": round(self.retrieval_latency_ms, 2),
            "llm_latency_ms": round(self.llm_latency_ms, 2),
            "end_to_end_latency_ms": round(self.end_to_end_latency_ms, 2),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
        }
        return data


# ---------------------------------------------------------------------------
def load_cases(path: Path, category: str | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        raise SystemExit(
            f"No evaluation cases at {path}.\n"
            f"Generate them with:  python scripts/generate_corpus.py"
        )
    cases: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"  skipping cases.jsonl line {number}: {exc.msg}")
            continue
        if category and case.get("category") != category:
            continue
        cases.append(case)
    return cases


def external_id_map(user_id: str) -> dict[int, str]:
    """transcript row id -> the id it was imported under."""
    rows = store.get_connection().execute(
        "SELECT id, external_id FROM transcripts WHERE user_id = ?", (user_id,)
    ).fetchall()
    return {int(r["id"]): (r["external_id"] or str(r["id"])) for r in rows}


def transcript_by_external(user_id: str, external_id: str) -> dict[str, Any] | None:
    row = store.get_connection().execute(
        "SELECT * FROM transcripts WHERE user_id = ? AND external_id = ?", (user_id, external_id)
    ).fetchone()
    return store.row_to_dict(row) if row else None


def memories_from_transcript(transcript_id: int) -> list[dict[str, Any]]:
    rows = store.get_connection().execute(
        "SELECT * FROM memories WHERE source_transcript_id = ?", (transcript_id,)
    ).fetchall()
    return store.rows_to_dicts(rows)


# ---------------------------------------------------------------------------
# Case evaluation
# ---------------------------------------------------------------------------
def evaluate_state_case(case: dict[str, Any], user_id: str) -> CaseResult:
    """Cases that inspect what the pipeline stored, rather than asking a question."""
    result = CaseResult(
        case_id=case["id"],
        category=case["category"],
        question=None,
        expected_behavior=case.get("expected_behavior", ""),
    )

    if external := case.get("check_no_memory_from"):
        transcript = transcript_by_external(user_id, external)
        if transcript is None:
            result.failures.append(f"transcript {external!r} is not in the database")
        else:
            memories = [
                m
                for m in memories_from_transcript(transcript["id"])
                if m["status"] in ("ACTIVE", "SUPERSEDED")
            ]
            run = store.extraction_run_for(transcript["id"])
            result.answer = (
                f"extraction decision = {run['decision'] if run else 'none'}; "
                f"{len(memories)} durable memory/memories created"
            )
            if memories:
                result.failures.append(
                    f"expected no durable memory, but {len(memories)} was/were created: "
                    + "; ".join(m["content"][:60] for m in memories)
                )

    if external := case.get("check_superseded_from"):
        transcript = transcript_by_external(user_id, external)
        if transcript is None:
            result.failures.append(f"transcript {external!r} is not in the database")
        else:
            memories = memories_from_transcript(transcript["id"])
            statuses = {m["status"] for m in memories}
            result.answer = f"statuses of memories from {external}: {sorted(statuses) or ['none']}"
            if not memories:
                result.failures.append("no memory was created from the transcript being corrected")
            elif "SUPERSEDED" not in statuses:
                result.failures.append(
                    f"expected a SUPERSEDED memory, found {sorted(statuses)}"
                )

    if external := case.get("check_duplicate_from"):
        transcript = transcript_by_external(user_id, external)
        if transcript is None:
            result.failures.append(f"transcript {external!r} is not in the database")
        else:
            memories = [
                m for m in memories_from_transcript(transcript["id"]) if m["status"] != "REJECTED"
            ]
            run = store.extraction_run_for(transcript["id"])
            duplicates = run["memories_duplicate"] if run else 0
            result.answer = (
                f"{len(memories)} memory/memories stored, {duplicates} flagged as duplicate"
            )
            if memories and not duplicates:
                result.failures.append(
                    "the repeated dictation created a new memory instead of being "
                    "recognised as a duplicate"
                )

    result.passed = not result.failures
    return result


def evaluate_query_case(
    case: dict[str, Any], user_id: str, id_to_external: dict[int, str]
) -> CaseResult:
    """Cases that ask Hey Kivi a question and check the answer."""
    question = case["question"]
    result = CaseResult(
        case_id=case["id"],
        category=case["category"],
        question=question,
        expected_behavior=case.get("expected_behavior", ""),
        expected_sources=list(case.get("expect_sources_any") or []),
    )

    started = time.perf_counter()
    answer = ask(question, user_id=user_id, persist=False)
    result.end_to_end_latency_ms = (time.perf_counter() - started) * 1000

    result.answer = answer.answer
    result.abstained = answer.abstained
    result.conflict = answer.conflict
    result.supported = answer.supported
    result.confidence = answer.confidence
    result.reasoning = answer.reasoning
    result.retrieved_memory_ids = answer.retrieved_memory_ids
    result.used_memory_ids = answer.used_memory_ids
    result.retrieval_latency_ms = answer.retrieval_latency_ms
    result.llm_latency_ms = answer.llm_latency_ms
    result.input_tokens = answer.input_tokens
    result.output_tokens = answer.output_tokens
    result.estimated_cost_usd = answer.cost_usd

    # Which source transcripts did retrieval reach, and which did the answer use?
    retrieved = store.get_memories(answer.retrieved_memory_ids)
    result.retrieved_sources = sorted(
        {
            id_to_external.get(m["source_transcript_id"], "")
            for m in retrieved
            if m.get("source_transcript_id")
        }
        - {""}
    )
    result.used_sources = sorted(
        {s.transcript_id and id_to_external.get(s.transcript_id, "") for s in answer.sources} - {"", None}
    )

    lowered = answer.answer.lower()

    # --- abstention -------------------------------------------------------
    if case.get("expect_abstain") is True:
        if not answer.abstained:
            result.failures.append(
                "expected Kivi to abstain, but it answered: " + answer.answer[:160]
            )
    elif case.get("expect_abstain") is False:
        if answer.abstained:
            result.failures.append("expected an answer, but Kivi abstained")

    # --- conflict ---------------------------------------------------------
    if case.get("expect_conflict") is True and not answer.conflict:
        result.failures.append("expected the conflict to be flagged, but it was not")
    if case.get("expect_conflict") is False and answer.conflict:
        result.failures.append("a conflict was flagged where none was expected")

    # --- answer content ---------------------------------------------------
    for needle in case.get("expect_answer_contains_all") or []:
        if needle.lower() not in lowered:
            result.failures.append(f"answer is missing required text {needle!r}")

    any_of = case.get("expect_answer_contains_any") or []
    if any_of and not any(n.lower() in lowered for n in any_of):
        result.failures.append(f"answer contains none of {any_of}")

    for needle in case.get("expect_answer_excludes") or []:
        if needle.lower() in lowered:
            result.failures.append(
                f"answer contains {needle!r}, which should have been superseded"
            )

    max_words = case.get("expect_answer_max_words")
    if max_words and len(answer.answer.split()) > int(max_words):
        result.failures.append(
            f"answer is {len(answer.answer.split())} words, expected at most {max_words}"
        )

    # --- provenance -------------------------------------------------------
    min_sources = case.get("expect_min_sources")
    if min_sources and len(answer.used_memory_ids) < int(min_sources):
        result.failures.append(
            f"answer cites {len(answer.used_memory_ids)} memory/memories, "
            f"expected at least {min_sources}"
        )

    expected_sources = case.get("expect_sources_any") or []
    if expected_sources:
        result.source_retrieved = any(e in result.retrieved_sources for e in expected_sources)
        result.source_used = any(e in result.used_sources for e in expected_sources)

        # Whether a missed source is a *failure* depends on whether anything
        # else in the case proves the answer was right. A 500-dictation corpus
        # of one person's work naturally contains several transcripts
        # supporting the same true answer; insisting on the one we happened to
        # write the case around would be testing the corpus, not the system.
        # Both source signals are always recorded as metrics either way.
        content_asserted = any(
            key in case
            for key in (
                "expect_answer_contains_all",
                "expect_answer_contains_any",
                "expect_answer_excludes",
            )
        )
        strict_sources = case["category"] == "provenance" or not content_asserted

        if not result.source_retrieved:
            if strict_sources:
                result.failures.append(
                    f"retrieval never reached any of the expected transcripts {expected_sources}"
                )
        elif not result.source_used:
            # Retrieved but cited something else. That is only a failure when
            # the citation is the behaviour under test, or when nothing else in
            # the case proves the answer was right - a corpus of 500 real
            # dictations often contains a second transcript that supports the
            # same correct answer, and marking that wrong would be testing the
            # corpus rather than the system. It is always counted in the
            # used-source precision metric.
            if strict_sources:
                result.failures.append(
                    f"the expected transcript was retrieved but not used "
                    f"(used: {result.used_sources or 'none'})"
                )

    # --- grounding --------------------------------------------------------
    # An answer that is neither an abstention nor supported by its own citations
    # is the failure mode this whole product exists to avoid.
    if not answer.abstained and not answer.supported:
        result.failures.append(f"answer is unsupported by the memories it cites ({answer.reasoning})")

    result.passed = not result.failures
    return result


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def compute_metrics(results: list[CaseResult], user_id: str) -> dict[str, Any]:
    def rate(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator else None

    query_results = [r for r in results if r.question is not None]
    abstention_cases = [r for r in results if r.category == "abstention"]
    grounded_cases = [r for r in query_results if r.category != "abstention"]
    conflict_cases = [r for r in results if r.category == "conflict"]
    irrelevant_cases = [r for r in results if r.category == "irrelevant"]
    update_cases = [r for r in results if r.category in ("correction", "memory_update")]
    with_sources = [r for r in query_results if r.expected_sources]

    latencies = [r.end_to_end_latency_ms for r in query_results if r.end_to_end_latency_ms]
    retrieval_latencies = [r.retrieval_latency_ms for r in query_results if r.retrieval_latency_ms]

    # Extraction behaviour over the whole corpus, not just the cases.
    totals = store.extraction_totals()
    counts = store.memory_counts(user_id)
    transcripts = store.count_transcripts(user_id)

    metrics: dict[str, Any] = {
        "cases_total": len(results),
        "cases_passed": sum(1 for r in results if r.passed),
        "pass_rate": rate(sum(1 for r in results if r.passed), len(results)),
        "by_category": {},
        # --- truthfulness -------------------------------------------------
        "correct_abstention_rate": rate(
            sum(1 for r in abstention_cases if r.abstained), len(abstention_cases)
        ),
        "false_abstention_rate": rate(
            sum(1 for r in grounded_cases if r.abstained), len(grounded_cases)
        ),
        "supported_answer_rate": rate(
            sum(1 for r in grounded_cases if not r.abstained and r.supported),
            sum(1 for r in grounded_cases if not r.abstained),
        ),
        "hallucination_rate": rate(
            sum(1 for r in grounded_cases if not r.abstained and not r.supported),
            len(grounded_cases),
        ),
        "conflict_handling_accuracy": rate(
            sum(1 for r in conflict_cases if r.passed), len(conflict_cases)
        ),
        "memory_update_accuracy": rate(
            sum(1 for r in update_cases if r.passed), len(update_cases)
        ),
        "ignore_accuracy": rate(sum(1 for r in irrelevant_cases if r.passed), len(irrelevant_cases)),
        # --- retrieval ----------------------------------------------------
        "retrieval_recall_at_k": rate(
            sum(1 for r in with_sources if r.source_retrieved), len(with_sources)
        ),
        "used_source_precision": rate(
            sum(1 for r in with_sources if r.source_used), len(with_sources)
        ),
        # --- cost and speed ----------------------------------------------
        "avg_retrieval_latency_ms": round(statistics.fmean(retrieval_latencies), 2)
        if retrieval_latencies
        else None,
        "avg_end_to_end_latency_ms": round(statistics.fmean(latencies), 2) if latencies else None,
        "p95_end_to_end_latency_ms": round(
            sorted(latencies)[int(len(latencies) * 0.95) - 1], 2
        )
        if len(latencies) >= 2
        else None,
        "avg_input_tokens": round(
            statistics.fmean([r.input_tokens for r in query_results]), 1
        )
        if query_results
        else 0,
        "avg_output_tokens": round(
            statistics.fmean([r.output_tokens for r in query_results]), 1
        )
        if query_results
        else 0,
        "total_query_cost_usd": round(sum(r.estimated_cost_usd for r in query_results), 6),
        # --- what the pipeline did to the corpus --------------------------
        "corpus": {
            "transcripts": transcripts,
            "extraction_runs": totals.get("runs", 0),
            "remembered": totals.get("remembered", 0),
            "ignored": totals.get("ignored", 0),
            "ignore_share": rate(totals.get("ignored", 0) or 0, totals.get("runs", 0) or 0),
            "memories_created": totals.get("created", 0),
            "memories_superseded": totals.get("superseded", 0),
            "memories_duplicate": totals.get("duplicate", 0),
            "memories_rejected": totals.get("rejected", 0),
            "memory_store": counts,
            "memories_per_transcript": round(
                (totals.get("created", 0) or 0) / transcripts, 3
            )
            if transcripts
            else 0,
            "extraction_cost_usd": round(totals.get("cost_usd", 0) or 0, 6),
            "avg_extraction_latency_ms": round(totals.get("avg_latency_ms", 0) or 0, 2),
        },
    }

    categories: dict[str, list[CaseResult]] = {}
    for result in results:
        categories.setdefault(result.category, []).append(result)
    for name, group in sorted(categories.items()):
        metrics["by_category"][name] = {
            "total": len(group),
            "passed": sum(1 for r in group if r.passed),
            "pass_rate": rate(sum(1 for r in group if r.passed), len(group)),
        }

    return metrics


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    metrics = payload["metrics"]
    run = payload["run"]
    lines: list[str] = [
        "# Evaluation results",
        "",
        f"- **Run at** {run['started_at']}",
        f"- **Reasoning engine** `{run['provider']}` (`{run['model']}`)",
        f"- **Embeddings** `{run['embedding_provider']}`",
        f"- **Cases** {metrics['cases_passed']}/{metrics['cases_total']} passed "
        f"({(metrics['pass_rate'] or 0) * 100:.1f}%)",
        "",
        "## Headline metrics",
        "",
        "| Metric | Value | What it means |",
        "| --- | --- | --- |",
    ]

    def row(label: str, key: str, meaning: str, percent: bool = True) -> str:
        value = metrics.get(key)
        if value is None:
            shown = "n/a"
        elif percent:
            shown = f"{value * 100:.1f}%"
        else:
            shown = f"{value}"
        return f"| {label} | {shown} | {meaning} |"

    lines += [
        row("Overall pass rate", "pass_rate", "Cases meeting every expectation."),
        row(
            "Correct abstention",
            "correct_abstention_rate",
            "Questions with no supporting memory that Kivi refused to answer.",
        ),
        row(
            "False abstention",
            "false_abstention_rate",
            "Answerable questions Kivi wrongly refused. Lower is better.",
        ),
        row(
            "Supported answer rate",
            "supported_answer_rate",
            "Answers whose content is backed by the memories they cite.",
        ),
        row(
            "Hallucination rate",
            "hallucination_rate",
            "Answers that were neither abstentions nor supported. Lower is better.",
        ),
        row("Retrieval recall@k", "retrieval_recall_at_k", "Expected source reached by retrieval."),
        row(
            "Used-source precision",
            "used_source_precision",
            "Expected source actually cited in the answer.",
        ),
        row("Conflict handling", "conflict_handling_accuracy", "Live disagreements surfaced, not resolved silently."),
        row("Memory update accuracy", "memory_update_accuracy", "Corrections superseded the old value."),
        row("Ignore accuracy", "ignore_accuracy", "Filler that correctly produced no memory."),
        "",
        "## Speed and cost",
        "",
        f"- Average retrieval latency: **{metrics.get('avg_retrieval_latency_ms')} ms**",
        f"- Average end-to-end latency: **{metrics.get('avg_end_to_end_latency_ms')} ms**",
        f"- p95 end-to-end latency: **{metrics.get('p95_end_to_end_latency_ms')} ms**",
        f"- Average tokens per query: **{metrics.get('avg_input_tokens')} in / "
        f"{metrics.get('avg_output_tokens')} out**",
        f"- Total cost of this query suite: **${metrics.get('total_query_cost_usd')}**",
        "",
    ]

    growth = payload.get("database_growth")
    if growth:
        lines += [
            "## Database growth",
            "",
            f"- Size on disk: **{growth['bytes_after'] / 1024:.0f} KiB** "
            f"({growth['bytes_added'] / 1024:+.0f} KiB during this run)",
            f"- Rows: **{growth['rows_after']}** "
            f"({growth['rows_added']:+d} during this run)",
            "",
            "| Table | Rows |",
            "| --- | ---: |",
            *[f"| `{name}` | {count} |" for name, count in sorted(growth["tables_after"].items())],
            "",
            "The query suite only reads memory, so a run adds evaluation rows rather than",
            "memories. Corpus ingestion is where the database actually grows — that figure",
            "is in the section below.",
            "",
        ]

    lines += [
        "## What the pipeline did to the corpus",
        "",
    ]
    corpus = metrics["corpus"]
    lines += [
        f"- Transcripts ingested: **{corpus['transcripts']}**",
        f"- Remembered: **{corpus['remembered']}**  |  deliberately ignored: "
        f"**{corpus['ignored']}** ({(corpus['ignore_share'] or 0) * 100:.1f}%)",
        f"- Memories created: **{corpus['memories_created']}** "
        f"({corpus['memories_per_transcript']} per transcript)",
        f"- Superseded by a correction: **{corpus['memories_superseded']}**",
        f"- Skipped as duplicates: **{corpus['memories_duplicate']}**",
        f"- Rejected below the confidence threshold: **{corpus['memories_rejected']}**",
        f"- Memory store by status: `{corpus['memory_store']}`",
        f"- Average extraction latency: **{corpus['avg_extraction_latency_ms']} ms** per transcript",
        f"- Extraction cost: **${corpus['extraction_cost_usd']}**",
        "",
        "## By category",
        "",
        "| Category | Passed | Total | Rate |",
        "| --- | --- | --- | --- |",
    ]
    for name, stats in metrics["by_category"].items():
        lines.append(
            f"| {name} | {stats['passed']} | {stats['total']} | "
            f"{(stats['pass_rate'] or 0) * 100:.0f}% |"
        )

    failures = [c for c in payload["cases"] if not c["passed"]]
    lines += ["", f"## Failures ({len(failures)})", ""]
    if not failures:
        lines.append("None.")
    for case in failures:
        lines += [
            f"### {case['test_id']} — {case['category']}",
            "",
            f"- **Question:** {case['question'] or '(state check, no question)'}",
            f"- **Expected:** {case['expected_behavior']}",
            f"- **Answer:** {case['answer'][:400] or '(none)'}",
            "- **Why it failed:**",
        ]
        for failure in case["failures"]:
            lines.append(f"  - {failure}")
        lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
def db_snapshot() -> dict[str, Any]:
    """Size on disk and row counts, so the suite can report database growth.

    The assignment asks for database growth alongside latency, model usage and
    cost. Growth is measured rather than estimated: the file size and the row
    count of every table, taken before and after the run. `page_count` is read
    too, because the file size alone under-reports — SQLite reuses free pages
    before it grows the file, so a run can add rows without adding bytes.
    """
    path = get_settings().db_path
    snapshot: dict[str, Any] = {
        "bytes": path.stat().st_size if path.exists() else 0,
        "tables": {},
    }
    with sqlite3.connect(path) as conn:
        snapshot["page_count"] = conn.execute("PRAGMA page_count").fetchone()[0]
        snapshot["page_size"] = conn.execute("PRAGMA page_size").fetchone()[0]
        names = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        for name in names:
            snapshot["tables"][name] = conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
    return snapshot


def db_growth(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """The difference between two snapshots, plus per-row cost of the run."""
    rows_before = sum(before["tables"].values())
    rows_after = sum(after["tables"].values())
    added = {
        name: after["tables"][name] - before["tables"].get(name, 0)
        for name in after["tables"]
        if after["tables"][name] != before["tables"].get(name, 0)
    }
    grew = after["bytes"] - before["bytes"]
    return {
        "bytes_before": before["bytes"],
        "bytes_after": after["bytes"],
        "bytes_added": grew,
        "pages_added": after["page_count"] - before["page_count"],
        "rows_before": rows_before,
        "rows_after": rows_after,
        "rows_added": rows_after - rows_before,
        "rows_added_by_table": added,
        "bytes_per_row_added": (
            round(grew / (rows_after - rows_before), 1) if rows_after != rows_before else None
        ),
        "tables_after": after["tables"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Kivi evaluation suite.")
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    parser.add_argument("--category", default=None, help="Run only one category.")
    parser.add_argument("--verbose", action="store_true", help="Print every case, not just failures.")
    parser.add_argument("--no-save", action="store_true", help="Do not write results to disk.")
    args = parser.parse_args()

    settings = get_settings()
    init_db()
    user_id = settings.default_user_id
    engine = get_engine()
    embedder = get_embedder()

    transcripts = store.count_transcripts(user_id)
    if transcripts == 0:
        raise SystemExit(
            "The database is empty, so there is nothing to evaluate.\n"
            "Run:  python scripts/seed.py"
        )
    pending = len(store.unprocessed_transcripts(user_id=user_id))
    if pending:
        print(f"warning: {pending} transcript(s) have not been processed yet.")
        print("         Run: python scripts/process_corpus.py\n")

    cases = load_cases(args.cases, args.category)
    if not cases:
        raise SystemExit("No cases matched.")

    print("=" * 74)
    print("Kivi Semantic Memory - evaluation")
    print("=" * 74)
    print(f"  engine     : {engine.name} ({engine.model})")
    print(f"  embeddings : {embedder.name} ({embedder.model}, {embedder.dim}d)")
    print(f"  transcripts: {transcripts}")
    print(f"  memories   : {store.memory_counts(user_id)}")
    print(f"  cases      : {len(cases)}")
    print()

    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    run_id = store.create_eval_run(
        started_at=started_at,
        provider=engine.name,
        model=engine.model,
        embedding_provider=embedder.name,
    )

    id_to_external = external_id_map(user_id)
    results: list[CaseResult] = []
    db_before = db_snapshot()
    suite_started = time.perf_counter()

    for case in cases:
        if any(key in case for key in STATE_CHECKS):
            result = evaluate_state_case(case, user_id)
        elif case.get("question"):
            result = evaluate_query_case(case, user_id, id_to_external)
        else:
            continue

        results.append(result)
        store.add_eval_result(
            run_id=run_id,
            case_id=result.case_id,
            category=result.category,
            passed=result.passed,
            detail=result.as_dict(),
        )

        mark = "PASS" if result.passed else "FAIL"
        if args.verbose or not result.passed:
            print(f"[{mark}] {result.case_id}  ({result.category})")
            if result.question:
                print(f"       Q: {result.question}")
            print(f"       A: {(result.answer or '(none)')[:200]}")
            for failure in result.failures:
                print(f"       ! {failure}")
            print()
        else:
            print(f"[{mark}] {result.case_id}  ({result.category})")

    suite_elapsed = time.perf_counter() - suite_started
    metrics = compute_metrics(results, user_id)

    store.finish_eval_run(
        run_id=run_id,
        finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        total_cases=len(results),
        metrics=metrics,
        notes=f"suite completed in {suite_elapsed:.1f}s",
    )

    print()
    print("=" * 74)
    print(
        f"  {metrics['cases_passed']}/{metrics['cases_total']} passed "
        f"({(metrics['pass_rate'] or 0) * 100:.1f}%) in {suite_elapsed:.1f}s"
    )
    print("=" * 74)
    for name, stats in metrics["by_category"].items():
        bar = "#" * int((stats["pass_rate"] or 0) * 20)
        print(
            f"  {name:18} {stats['passed']:3d}/{stats['total']:<3d} "
            f"{(stats['pass_rate'] or 0) * 100:5.1f}%  {bar}"
        )
    print()
    for key in (
        "correct_abstention_rate",
        "false_abstention_rate",
        "supported_answer_rate",
        "hallucination_rate",
        "retrieval_recall_at_k",
        "used_source_precision",
        "conflict_handling_accuracy",
        "memory_update_accuracy",
        "ignore_accuracy",
    ):
        value = metrics.get(key)
        shown = "n/a" if value is None else f"{value * 100:5.1f}%"
        print(f"  {key:28} {shown}")
    print()
    print(f"  avg retrieval latency  {metrics.get('avg_retrieval_latency_ms')} ms")
    print(f"  avg end-to-end latency {metrics.get('avg_end_to_end_latency_ms')} ms")
    print(f"  query suite cost       ${metrics.get('total_query_cost_usd')}")

    growth = db_growth(db_before, db_snapshot())
    print()
    print(
        f"  database               {growth['rows_after']} rows, "
        f"{growth['bytes_after'] / 1024:.0f} KiB "
        f"({growth['rows_added']:+d} rows, {growth['bytes_added'] / 1024:+.0f} KiB this run)"
    )

    payload = {
        "run": {
            "id": run_id,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "provider": engine.name,
            "model": engine.model,
            "embedding_provider": embedder.name,
            "embedding_model": embedder.model,
            "elapsed_seconds": round(suite_elapsed, 2),
        },
        "metrics": metrics,
        "database_growth": growth,
        "cases": [r.as_dict() for r in results],
    }

    if not args.no_save:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        for target in (RESULTS_DIR / "latest.json", RESULTS_DIR / f"run_{stamp}.json"):
            target.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        write_markdown(RESULTS_DIR / "latest.md", payload)
        print(f"\n  results -> evaluation/results/latest.json")
        print(f"             evaluation/results/latest.md")
        print(f"             evaluation/results/run_{stamp}.json")

    failed = metrics["cases_total"] - metrics["cases_passed"]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
