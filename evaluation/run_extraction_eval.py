"""Held-out extraction evaluation — does the extractor generalise?

    python evaluation/run_extraction_eval.py

WHY THIS EXISTS, SEPARATELY FROM run_eval.py
--------------------------------------------
`run_eval.py` measures the whole pipeline: a question goes in, an answer comes
out, and extraction, retrieval and answering are all being graded at once. When
it passes you cannot tell which stage earned the pass. And it runs against
`data/development_corpus.jsonl`, which was written alongside the extractor —
so its 96.2% partly measures how well the extractor fits its own corpus.

This measures ONE stage against dictations phrased in a deliberately different
voice: fragments ("Call with Dev pushed, now Thursday 3."), imperatives ("Keep
the changelog dry"), abbreviations, and short sentences the training corpus
does not contain. Nothing here shares a template with the corpus.

That matters because the assignment says the system will be run on a separate
corpus of ~500 dictations from a real user, and that the reviewers will examine
"which facts, preferences, and episodes Kivi learned". This is the number that
predicts that run. It is expected to be lower than the pipeline score; a
measured weakness is worth more than an unmeasured claim.

WHAT IS MEASURED
----------------
Per memory type, over the held-out set:

    recall     of the memories that should have been extracted, how many were
    precision  of the memories extracted, how many were expected

plus, separately, how reliably the extractor stays silent on filler — a false
positive there is a memory the user never asked for.

The labels are types, not exact wording. Two engines phrase the same memory
differently and both are right; what must not vary is whether a stated
preference is recognised as a preference at all.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from backend.llm.engine import get_engine  # noqa: E402

CASES_PATH = REPO_ROOT / "evaluation" / "heldout_extraction.jsonl"
RESULTS_DIR = REPO_ROOT / "evaluation" / "results"
TYPES = ["fact", "preference", "event", "task", "episode"]


def load(path: Path) -> list[dict[str, Any]]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    parser.add_argument("--verbose", action="store_true", help="Print every record.")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    engine = get_engine()
    records = load(args.cases)

    # Counted as multisets per type: a dictation expecting one event and
    # producing two events is one hit and one false positive, not a pass.
    hits: dict[str, int] = defaultdict(int)
    expected: dict[str, int] = defaultdict(int)
    produced: dict[str, int] = defaultdict(int)
    silence_ok = silence_total = 0
    rows: list[dict[str, Any]] = []

    print("=" * 78)
    print(" Held-out extraction — dictations the extractor was not tuned on")
    print("=" * 78)
    print(f"  engine : {engine.name} ({engine.model})")
    print(f"  records: {len(records)}\n")

    for record in records:
        want = list(record["expect_types"])
        result = engine.extract(
            formatted_text=record["formatted_output"],
            raw_asr=record["raw_asr"],
            timestamp=record["timestamp"],
            application=record.get("application"),
        )
        got = [m.type for m in result.memories]

        remaining = list(want)
        matched = []
        for t in got:
            if t in remaining:
                remaining.remove(t)
                matched.append(t)
        for t in matched:
            hits[t] += 1
        for t in want:
            expected[t] += 1
        for t in got:
            produced[t] += 1

        if not want:
            silence_total += 1
            if not got:
                silence_ok += 1

        ok = sorted(got) == sorted(want)
        rows.append(
            {"id": record["id"], "text": record["formatted_output"],
             "expected": want, "got": got, "passed": ok}
        )
        if args.verbose or not ok:
            mark = "ok  " if ok else "MISS"
            print(f"  [{mark}] {record['id']}  want={want or '-'}  got={got or '-'}")
            print(f"         {record['formatted_output'][:68]}")

    print("\n" + "-" * 78)
    print(f"  {'type':<12}{'recall':>10}{'precision':>12}   expected / produced")
    print("-" * 78)
    for t in TYPES:
        e, p, h = expected[t], produced[t], hits[t]
        rec = h / e if e else None
        pre = h / p if p else None
        print(
            f"  {t:<12}"
            f"{('  n/a' if rec is None else f'{rec*100:9.0f}%')}"
            f"{('     n/a' if pre is None else f'{pre*100:11.0f}%')}"
            f"     {e:>6} / {p}"
        )

    total_e = sum(expected.values())
    total_h = sum(hits.values())
    total_p = sum(produced.values())
    overall_recall = total_h / total_e if total_e else 0.0
    overall_precision = total_h / total_p if total_p else 0.0
    exact = sum(1 for r in rows if r["passed"])

    print("-" * 78)
    print(f"  {'ALL':<12}{overall_recall*100:9.0f}%{overall_precision*100:11.0f}%"
          f"     {total_e:>6} / {total_p}")
    print()
    print(f"  records extracted exactly right : {exact}/{len(records)}"
          f"  ({exact/len(records)*100:.0f}%)")
    print(f"  filler correctly ignored        : {silence_ok}/{silence_total}")

    payload = {
        "engine": {"provider": engine.name, "model": engine.model},
        "records": len(records),
        "overall": {
            "recall": round(overall_recall, 4),
            "precision": round(overall_precision, 4),
            "exact_records": exact,
            "silence_correct": silence_ok,
            "silence_total": silence_total,
        },
        "by_type": {
            t: {
                "expected": expected[t],
                "produced": produced[t],
                "hits": hits[t],
                "recall": round(hits[t] / expected[t], 4) if expected[t] else None,
                "precision": round(hits[t] / produced[t], 4) if produced[t] else None,
            }
            for t in TYPES
        },
        "cases": rows,
    }

    if not args.no_save:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        # The engine is in the filename so the offline and LLM runs sit side by
        # side rather than overwriting each other — the comparison between them
        # is the point of this suite.
        target = RESULTS_DIR / f"heldout_extraction_{engine.name}.json"
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\n  results -> evaluation/results/{target.name}")

    # Reporting only. This measures generalisation, which is expected to be
    # imperfect; failing a build on it would only invite tuning to the held-out
    # set, which would destroy the one thing that makes it worth measuring.
    return 0


if __name__ == "__main__":
    sys.exit(main())
