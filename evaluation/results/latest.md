# Evaluation results

- **Run at** 2026-09-03T10:40:12+00:00
- **Reasoning engine** `heuristic` (`heuristic`)
- **Embeddings** `hashing`
- **Cases** 50/52 passed (96.2%)

## Headline metrics

| Metric | Value | What it means |
| --- | --- | --- |
| Overall pass rate | 96.2% | Cases meeting every expectation. |
| Correct abstention | 100.0% | Questions with no supporting memory that Kivi refused to answer. |
| False abstention | 0.0% | Answerable questions Kivi wrongly refused. Lower is better. |
| Supported answer rate | 100.0% | Answers whose content is backed by the memories they cite. |
| Hallucination rate | 0.0% | Answers that were neither abstentions nor supported. Lower is better. |
| Retrieval recall@k | 96.4% | Expected source reached by retrieval. |
| Used-source precision | 96.4% | Expected source actually cited in the answer. |
| Conflict handling | 66.7% | Live disagreements surfaced, not resolved silently. |
| Memory update accuracy | 100.0% | Corrections superseded the old value. |
| Ignore accuracy | 100.0% | Filler that correctly produced no memory. |

## Speed and cost

- Average retrieval latency: **101.49 ms**
- Average end-to-end latency: **110.5 ms**
- p95 end-to-end latency: **134.16 ms**
- Average tokens per query: **0.0 in / 0.0 out**
- Total cost of this query suite: **$0.0**

## Database growth

- Size on disk: **2592 KiB** (+0 KiB during this run)
- Rows: **2493** (+52 during this run)

| Table | Rows |
| --- | ---: |
| `eval_results` | 416 |
| `eval_runs` | 8 |
| `extraction_runs` | 504 |
| `memories` | 394 |
| `memory_events` | 583 |
| `memory_relations` | 79 |
| `query_logs` | 4 |
| `schema_version` | 1 |
| `transcripts` | 504 |

The query suite only reads memory, so a run adds evaluation rows rather than
memories. Corpus ingestion is where the database actually grows — that figure
is in the section below.

## What the pipeline did to the corpus

- Transcripts ingested: **504**
- Remembered: **470**  |  deliberately ignored: **34** (6.8%)
- Memories created: **305** (0.605 per transcript)
- Superseded by a correction: **28**
- Skipped as duplicates: **76**
- Rejected below the confidence threshold: **10**
- Memory store by status: `{'ACTIVE': 356, 'REJECTED': 10, 'SUPERSEDED': 28}`
- Average extraction latency: **139.18 ms** per transcript
- Extraction cost: **$0.003232**

## By category

| Category | Passed | Total | Rate |
| --- | --- | --- | --- |
| abstention | 9 | 9 | 100% |
| conflict | 2 | 3 | 67% |
| correction | 6 | 6 | 100% |
| duplicate | 1 | 1 | 100% |
| fact | 6 | 6 | 100% |
| irrelevant | 7 | 7 | 100% |
| memory_update | 1 | 1 | 100% |
| multi_transcript | 4 | 4 | 100% |
| preference | 4 | 5 | 80% |
| provenance | 3 | 3 | 100% |
| retrieval | 7 | 7 | 100% |

## Failures (2)

### eval_008 — preference

- **Question:** How do I prefer my meeting summaries?
- **Expected:** Bullet points.
- **Answer:** I like meeting summaries to open with the decision, not the background. I prefer internal notes written in a plain, direct style.
- **Why it failed:**
  - answer contains none of ['bullet']

### eval_402 — conflict

- **Question:** When is the Atlas pricing sign-off with Sarah?
- **Expected:** Two times exist with no correction between them; say so.
- **Answer:** Sarah has the Atlas pricing sign-off down for Friday at 9 AM.
- **Why it failed:**
  - expected the conflict to be flagged, but it was not

