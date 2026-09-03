# Kivi Semantic Memory

**Kivi learns durable work context from a user's past dictations, and Hey Kivi
uses it to answer questions — citing the memories behind every answer, and
saying plainly when it does not know.**

> **Reviewers: start with [RUN.md](RUN.md).** It runs locally with no API key,
> no network and no cost. `python scripts/seed.py` then
> `python evaluation/run_eval.py` gets you from a clean clone to the full
> evaluation in under a minute.

---

## Contents

- [What this is](#what-this-is)
- [The use case](#the-use-case)
- [Architecture](#architecture)
- [The memory model](#the-memory-model)
- [How a memory is created — and what is ignored](#how-a-memory-is-created--and-what-is-ignored)
- [How corrections work](#how-corrections-work)
- [Retrieval](#retrieval)
  - [The rescue: when nothing was learned](#the-rescue-when-nothing-was-learned)
- [How Hey Kivi answers](#how-hey-kivi-answers)
- [Provenance](#provenance)
- [User control](#user-control)
- [The screens](#the-screens)
- [The dataset](#the-dataset)
- [Evaluation](#evaluation)
- [Results](#results)
  - [Held-out extraction: does it generalise?](#held-out-extraction-does-it-generalise)
- [Where AI is used](#where-ai-is-used)
- [Limitations](#limitations)
- [Repository map](#repository-map)

---

## What this is

A working end-to-end product, not a prototype of one. Every behaviour comes from
stored state, real retrieval and real model decisions:

- 500 dictations are ingested and stored immutably.
- A model decides, per dictation, what — if anything — deserves to be
  remembered. Roughly 7% of the corpus is deliberately ignored.
- Memories are typed, given a subject and a slot, embedded, and reconciled
  against what is already known: new, duplicate, superseding, or contradicting.
- Questions are parsed, memories retrieved by a four-signal hybrid ranking, and
  answers generated **only** from what was retrieved.
- Every answer names the memories it used; every memory traces to the transcript
  that produced it; every transcript is the user's own words.
- When memory does not support an answer, Kivi says so. When memory disagrees
  with itself, Kivi says that too.

Nothing is hardcoded to the development corpus. Import a different one and the
whole product — including the suggested questions and the groupings on the
memory screen — follows the new data.

---

## The use case

> **Kivi remembers your work context — the people, the projects, what's
> scheduled, what you owe, and how you like things written — so you can ask
> what you need to prepare without repeating yourself.**

Deliberately narrow. Kivi does not try to be a general assistant, summarise the
user to themselves, infer mood, score relationships, or fill gaps from the
calendar. It answers questions about work context from dictations, and refuses
everything else.

---

## Architecture

```
                        ┌──────────────────────────┐
                        │  React + Vite frontend   │
                        │  History · Hey Kivi ·    │
                        │  Knows · Inspector       │
                        └────────────┬─────────────┘
                                     │  /api
                        ┌────────────▼─────────────┐
                        │      FastAPI backend     │
                        └────────────┬─────────────┘
              ┌──────────────────────┼──────────────────────┐
              ▼                      ▼                      ▼
      Transcript ingestion    Memory engine           Hey Kivi query
      (immutable store)       extract → resolve       plan → retrieve
              │               → embed → store         → answer → verify
              │                                       └ abstained? rescue
              │                                         from raw dictations
              └──────────────────────┼──────────────────────┘
                                     ▼
                        ┌──────────────────────────┐
                        │   SQLite (data/kivi.db)  │
                        │  transcripts · memories  │
                        │  relations · events      │
                        │  extraction_runs         │
                        │  query_logs · eval_runs  │
                        └────────────┬─────────────┘
                                     ▼
                       Answer + memories used + source
                       transcripts + decision + latency + cost
```

**Stack.** React 18 + Vite · FastAPI + Python 3.11+ · SQLite (stdlib `sqlite3`,
no ORM) · pluggable reasoning engine · pluggable embeddings.

### Two engines behind one interface

Every model-driven decision goes through `ReasoningEngine`
(`backend/llm/engine.py`), which has exactly three operations: **extract**,
**resolve**, **answer**. Two implementations satisfy it.

| | `LLMEngine` | `HeuristicEngine` (default) |
| --- | --- | --- |
| Backed by | Claude / GPT / Gemini, structured JSON output | Deterministic rules |
| Needs a key | yes | **no** |
| Cost | metered per call | zero |
| Reproducible | approximately | exactly |
| Can invent a fact | yes — which is why grounding is enforced | **structurally no** — every sentence it emits is assembled from stored memory text |

The offline engine is the default so a reviewer can run everything immediately.
It is not a language model and does not pretend to be: it reads cue words and
sentence shapes, so it misses memories phrased in ways it has no rule for.
Switching to a real model is one line in `.env`; nothing else changes, and the
same evaluation runs against either.

If a configured provider is unavailable — missing key, no network, a refusal —
the system logs it and falls back to the offline engine for that item rather
than losing the record.

---

## The memory model

Five types, chosen because they behave differently rather than to be exhaustive:

| Type | What it holds | Example |
| --- | --- | --- |
| `fact` | A durable truth | *Sarah is the finance lead and signs off on any pricing change.* |
| `preference` | How the user wants things done | *Keep my client emails short and to the point.* |
| `event` | Something scheduled | *Beacon empty-state walkthrough with Priya is on Thursday at 4 PM.* |
| `task` | A commitment the user made | *I owe Sarah the churn assumptions before Monday.* |
| `episode` | Something discussed at a point in time | *Aditi found that warehouse costs on Cobalt tripled after the schema change.* |

Every memory carries a **subject** (usually a person or project) and an
**attribute** — the slot it fills: `meeting_time`, `deadline`, `role`,
`email_style`, `deliverable`, and so on. The slot is what makes correction
possible: a later dictation giving a different `meeting_time` for the same
subject is not new information, it is an update to a specific belief.

### Four statuses, and nothing is ever deleted

| Status | Meaning | Retrieved? |
| --- | --- | --- |
| `ACTIVE` | Kivi's current belief | yes |
| `SUPERSEDED` | Replaced by a correction | retrieved but heavily demoted; never answers |
| `REJECTED` | Extracted but below the confidence threshold | never |
| `DELETED` | The user asked Kivi to forget it | never |

Keeping superseded and rejected rows is a deliberate product decision. It is
what lets Kivi say *"it was moved from 3 PM"*, lets the user put back something
they forgot by mistake, and lets a reviewer see what the system chose **not** to
believe — which is usually more interesting than what it kept.

### Schema

`migrations/001_initial.sql`. Seven tables:

- **`transcripts`** — the immutable record of what was said. The root of all
  provenance; never rewritten.
- **`memories`** — durable understanding. Every row has `source_transcript_id`.
- **`memory_relations`** — typed links: `SUPERSEDES`, `CONTRADICTS`,
  `DUPLICATE_OF`.
- **`memory_events`** — append-only audit log. Why every memory looks the way it
  does, and who did it (`system` or `user`).
- **`extraction_runs`** — one row per dictation processed, with the decision,
  the rationale, tokens, latency and cost.
- **`query_logs`** — one row per Hey Kivi turn, with the full retrieval ranking.
- **`eval_runs` / `eval_results`** — persisted evaluation runs.

---

## How a memory is created — and what is ignored

```
transcript
   → extract()          what, if anything, is worth remembering?
   → confidence gate    below threshold → stored as REJECTED, never retrieved
   → slot lookup        what do we already believe about this subject+attribute?
   → resolve()          NEW | DUPLICATE | SUPERSEDES | CONFLICTS
   → write memory + relation + audit event
   → mark transcript processed
```

**Not saving everything is the point.** On the 500-record corpus:

| | Count |
| --- | --- |
| Transcripts ingested | 500 |
| Produced at least one memory | 466 |
| **Deliberately ignored** | **34** (6.8%) |
| Memories created | 304 (0.61 per transcript) |
| Skipped as duplicates | 76 |
| Superseded by a correction | 25 |
| Flagged as conflicts (both kept) | 51 |
| Rejected below the confidence threshold | 10 |

Three things are ignored:

1. **Thinking aloud** — *"Hmm, okay, give me a second."*, *"Testing one two."*
2. **Content being dictated rather than stated** — a message body beginning
   *"Dear Rahul,"* is text the user is writing, not a fact about their work.
3. **Low-confidence extractions** — a vague sentence naming nobody and nothing
   is stored as `REJECTED` rather than acted on. It is visible in the UI, so
   the judgement can be reviewed and reversed.

Every ignored dictation writes an audit event with its reason, so "why is this
not in my memory?" is always answerable.

---

## How corrections work

Slots have different temporal semantics, and treating them alike is wrong in
both directions:

| Slot kind | Rule | Why |
| --- | --- | --- |
| `deadline`, `status`, `priority`, `budget` | Later value wins → **SUPERSEDES** | These have exactly one current value per subject. |
| `meeting_time`, `meeting_location` | Only with an explicit correction cue, or a genuine conflict | A person has *many* meetings. "Meeting with Rahul on Tuesday" does not replace "Meeting with Rahul on Friday". |
| `role`, `contact` | Scoped to one person | Two people holding different roles on one project is not a contradiction. |

For a meeting slot, the resolver compares what the two dictations are **about**,
ignoring the time words. Same appointment plus an explicit cue ("actually",
"move… to", "correction:") → supersede. Same appointment with no cue → a real
conflict, both kept. Different appointment → simply new.

**The slot key cannot be trusted to be stable.** `subject` is free text
produced by whichever engine did the extraction, and engines are not perfectly
consistent: the same appointment gets filed under "Priya" one day and "Priya
Vault rollout" the next. An exact `(subject, attribute)` match then loses the
correction *silently* — the new memory lands beside the old one, both stay
active, and nothing looks broken until Kivi reports a conflict about a meeting
that was simply moved. Slot lookup therefore tries an exact match first and a
containment match second (either direction, same attribute), and the engine's
`resolve` still makes the final call. Prompting for consistency helps; relying
on it does not.

Corrections are also rewritten into the statement they leave behind. *"Actually,
move the Beacon empty-state walkthrough with Priya to Thursday at 4 PM"* is an
instruction; six weeks later the useful memory is *"Beacon empty-state
walkthrough with Priya is on Thursday at 4 PM."* The original dictation is
untouched, and the resolver still matches on the original wording.

---

## Retrieval

Four signals, because each fails somewhere the others do not
(`backend/memory/retriever.py`):

| Signal | What it catches | Where it fails alone |
| --- | --- | --- |
| **Semantic** — cosine over embeddings | paraphrase, sub-word overlap | drifts on short questions |
| **Lexical** — BM25 | exact names and rare words | blind to rephrasing |
| **Recency** — 45-day half-life, boosted for time questions | "when is my meeting" should not surface March | buries durable facts |
| **Structure** — entity match, intent→type and intent→attribute boosts, extraction confidence, status | the strongest signal available | too coarse on its own |

Three details that mattered more than the weights:

**Reading the question comes before searching.** `backend/memory/query.py`
classifies intent (when / who / prepare / discussed / preference / draft / why),
extracts the entities named, and splits the remaining words into two sets that
do different jobs:

- **residual tokens** decide *abstention* — topic words that must appear
  somewhere in memory. *"When is Rahul's birthday"* leaves `birthday`, which
  appears nowhere, so Kivi refuses.
- **discriminative tokens** decide *ranking* — the words separating the right
  memory from the merely related. *"Who manages Project Cobalt"* and *"who is on
  Project Cobalt"* have the same entity and the same residuals; only the verb
  tells them apart.

**A named entity filters, it does not merely boost.** If Kivi knows about
Priya, a memory that never mentions Priya is not an answer to a question about
Priya, however similar it looks. Preference memories are exempt — *"keep my
client emails short"* is about the user, but it is exactly what should shape a
draft to Priya, so a slot is reserved for it.

**The question outranks its own expansion.** Intent expansions ("who" adds
*role*, *leads*, *team*…) are scored separately at 0.3 weight. Mixed into one
bag they win, and *"who is the finance lead"* matches every team-membership
sentence better than the one sentence containing *finance*.

### The rescue: when nothing was learned

Indexing memories rather than transcripts is what makes corrections work — one
slot holds one current value — but it has a structural cost. A dictation that
produced no memory, because extraction ignored it or rejected it below the
confidence threshold, is stored and permanently unreachable. A wrong judgement
there does not degrade the answer; it erases the content.

In this corpus that is **10 dictations of 500** — 2%. Not the 145 without a live
memory: 76 of those were duplicates whose content is reachable through the
original, 25 were superseded by the correction that replaced them, and 34 were
filler correctly dropped. The genuine loss is the 10 rejected, and they are not
benign — six are stated preferences of the form *"Use warm but direct tone when
I am writing to customer"*, misfiled as generic episodes at 0.4 confidence.

So retrieval has a second, gated path. **Memories answer; transcripts only
rescue.**

```
question → rank memories → answer
             ↓ only if that answer ABSTAINED
          rank raw dictations (BM25 over formatted text + ASR)
             ↓ only if this answer does NOT also abstain
          use it, labelled as never learned
```

Three properties make this safe rather than a second opinion:

**It triggers on abstention, not on empty retrieval.** Retrieval almost never
returns nothing — the failure that matters is returning eight near-misses and
then honestly refusing. Gating on emptiness looks reasonable and almost never
fires.

**Gating on abstention is what preserves reconciliation.** Transcript #3 still
reads *"Rahul is Monday at 10 AM"* long after memory #3 was superseded. Because
this path runs only when memories produced no answer, a stale transcript can
never appear beside the memory that replaced it. An ungated second index would
reintroduce exactly the contradiction that supersession exists to remove.

**A rescue is held to the memory standard.** The question's residual tokens must
appear in the text and named entities must match, identically to the memory
path. Without that check BM25 — which always returns its best match for any
input — answers *"what is my bank account number"* with whatever dictation
shares the most common words. The rescue exists to recover content, never to
lower the bar.

Rescued answers must open with *"I hadn't recorded this, but you said…"*. That
is enforced in the prompt rather than requested, because the model dropped it
when merely asked. Nothing has reconciled that sentence against anything said
later, so stating it as settled fact would claim a confidence the system does
not have. Citations show `dictation #31` rather than a memory id.

```text
Q: What neutral tone do I use?
A: I hadn't recorded this, but you said on 2026-06-24 that you use a neutral
   tone when writing to executives.          cites: dictation #31

Q: What is my bank account number?
A: I don't have anything about bank in your history.        still refuses
```

The first was unreachable before this path existed.

---

## How Hey Kivi answers

```
question → plan → retrieve → answer → verify → log
```

The answering prompt receives the question, the retrieved memories with their
status, and a short excerpt of each source transcript — so a claim can be
grounded in the user's actual words rather than only in Kivi's paraphrase. The
rules are explicit: use only the supplied memory; cite what you use; abstain if
it is not there; surface conflicts rather than resolving them.

**Then the answer is checked.** A model can produce fluent prose and cite the
wrong memories, or none. `_verify_support` measures how much of the answer's
content vocabulary actually comes from the memories it cited — with a
verbatim-quote shortcut, since an answer that quotes its evidence is supported by
definition. An answer that fails is reported as **unsupported** rather than
quietly presented as grounded. This check runs against both engines.

Three answer shapes, visually distinct in the UI because they mean different
things:

| | Example |
| --- | --- |
| **Grounded** | *"For Sarah, here's what your history has: I owe Sarah the churn assumptions before Monday. I need to send Sarah the updated figures before we meet."* |
| **Abstention** | *"I don't have anything about Rahul's birthday in your history. What I do have: Meeting with Rahul on Thursday about enterprise pricing."* |
| **Conflict** | *"I found 3 different answers in your history and I'm not confident which one is current: 'Monday at 10 AM'; 'Tuesday at 2 PM'. You may want to confirm which one still stands."* |

An abstention that also offers what Kivi *does* know is more useful than a bare
"I don't know", and still refuses to answer the question that was asked.

---

## Provenance

Every answer can be walked back to speech:

```
Answer
  └─ memory #386  "I owe Sarah the churn assumptions before Monday."   (task, ACTIVE, 0.88)
       └─ transcript #492                       2026-08-28 11:00 · Notes
            └─ "I owe Sarah the churn assumptions before Monday."
```

Available three ways: under every answer in the UI, as a step-by-step trace in
the Inspector, and over HTTP at `GET /api/memories/{id}` and
`GET /api/hey-kivi/queries/{id}`.

`query_logs` distinguishes memories **retrieved** from memories **used**, and
stores the per-signal score of every candidate — so "why did it pick that one?"
is answerable after the fact, not just live.

---

## User control

On **What Kivi Knows**, every memory can be:

- **Corrected** — edit the text. The embedding is recomputed, so retrieval
  follows the correction instead of continuing to find the old wording. The edit
  is logged with `actor='user'`, distinguishing what Kivi learned from what a
  person fixed.
- **Forgotten** — moves to `DELETED`. Excluded from every retrieval path, never
  destroyed.
- **Put back** — anything superseded, forgotten or rejected can be reinstated.

The screen speaks product language throughout: *Current* / *Replaced* /
*Forgotten* / *Not trusted*, and *Fact* / *Preference* / *Discussion* /
*Commitment* / *Scheduled*. No ids, embeddings, vector dimensions or prompt
internals appear on it. All of that is one screen over, in the Inspector, where
it belongs.

---

## The screens

| Screen | Purpose |
| --- | --- |
| **History** | The dictation feed, grouped by day. Opening one shows the raw ASR, the formatted text, and what Kivi learned — or why it learned nothing. New dictations can be added live. |
| **Hey Kivi** | The product. Question in, grounded answer out, memories used printed underneath. "Show working" reveals the full retrieval ranking. The composer also has a **Dictate** mode: speak into the same box and Kivi replies with what it decided to remember — or that it decided to remember nothing, and why. |
| **What Kivi Knows** | Current understanding grouped by people, projects, coming up, commitments and preferences — plus an archive of everything replaced or forgotten. |
| **Inspector** | For reviewers. The evaluation run with failures shown first, corpus statistics, the query log, and full provenance traces. |

Dictation appears on two screens on purpose. History is the archive — where
you go to look something up. Hey Kivi is the conversation, and the assignment
is explicit that dictation should become "one of the tools available inside
that interface" rather than a separate destination. Putting the composer there
closes the loop in one place: you say something, Kivi tells you what it took
from it, and you can ask about it in the next breath.

The interface takes Kivi's own design language from
[heykivi.ai](https://heykivi.ai) — the dusk palette, Fraunces / Switzer / Geist
Mono, the film grain over the ground — and stays dark throughout. Three rules
hold it together:

- **Elevation by tone, never by shadow.** A surface reads as raised because it
  is a shade lighter, not because something floats above the page. There is not
  one `box-shadow` in the stylesheet.
- **Separation by space and hairlines, not outlines.** Cards are areas of
  slightly different tone with room to breathe, not boxes drawn with a 1px
  stroke. Where a real division is needed, one low-contrast rule does it.
- **One accent, used sparingly.** The green marks the current screen, a focused
  field, and a figure worth reading — not every chip on the page.

---

## The dataset

`data/development_corpus.jsonl` — 500 records, one person's work over ten weeks:
twelve colleagues, six projects, meetings that move, numbers that get promised,
preferences stated once and expected to stick.

Generated deterministically by `scripts/generate_corpus.py` (fixed seed), which
emits the corpus **and** the evaluation cases together — an eval case asserting
"the answer should be 4 PM" is only meaningful if the corpus really contains a
3 PM meeting that was really moved. Distribution and record format:
[`docs/CORPUS_FORMAT.md`](docs/CORPUS_FORMAT.md).

`raw_asr` is generated by degrading the formatted text the way a recogniser
would — dropped function words, phonetic errors on names (`Rahul` → `rahool`,
`Atlas` → `atlus`), doubled and clipped words — so extraction and retrieval are
exercised against realistic input.

---

## Evaluation

```bash
python evaluation/run_eval.py
```

52 cases against the live system. Nothing stubbed: real storage, real retrieval,
real model decisions. Two kinds of case — questions asked through the full
pipeline, and **state checks** that inspect what the pipeline stored (did the
correction really supersede? did the filler really produce nothing? was the
repeat really recognised as a duplicate?).

Every failure is printed in full and written to the results file. Results go to
`evaluation/results/latest.json`, `latest.md`, a timestamped copy, and the
database — the Inspector renders the same run.

### How to read these numbers

Two caveats worth stating plainly:

1. **This suite was developed alongside the system**, not held out. It is a
   development suite: it catches regressions and documents intended behaviour.
   It is not evidence of generalisation. The honest test is your own corpus —
   see [RUN.md §8](RUN.md#8-import-a-different-corpus-reviewer-corpus).
2. **The 0% hallucination rate is partly architectural.** The default offline
   engine assembles answers from stored memory text and cannot write a sentence
   that is not already in memory. That is a real property worth having, but it
   is a weaker claim than an LLM achieving the same number. Re-run with
   `KIVI_LLM_PROVIDER=anthropic` to measure the harder case.

Source expectations are treated as metrics rather than hard failures when the
case already proves correctness another way: a 500-dictation corpus of one
person's work naturally contains several transcripts supporting the same true
answer, and insisting on the one the case was written around would test the
corpus rather than the system. `provenance` cases, where the citation *is* the
behaviour under test, are strict.

---

## Results

Reference run — offline engine, hashing embedder, 500-record corpus, on a laptop.
Reproduced exactly by `python scripts/seed.py && python evaluation/run_eval.py`.

**50 / 52 cases passed (96.2%)** in 4.3 seconds.

| Metric | Value | What it means |
| --- | --- | --- |
| Hallucination rate | **0.0%** | Answers neither abstaining nor supported by their citations |
| Supported answer rate | **100%** | Answer content backed by the memories it cites |
| Correct abstention | **100%** | Unanswerable questions correctly refused (9/9) |
| False abstention | **0.0%** | Answerable questions wrongly refused — on this corpus; see [the offline engine on unfamiliar phrasing](#the-offline-engine-on-unfamiliar-phrasing) |
| Retrieval recall@k | **96.4%** | Expected source transcript reached by retrieval |
| Used-source precision | **96.4%** | Expected source actually cited |
| Memory update accuracy | **100%** | Corrections superseded the value they replaced |
| Ignore accuracy | **100%** | Filler correctly produced no memory (7/7) |
| Conflict handling | **66.7%** | Live disagreements surfaced, not silently resolved (2/3) |
| Avg retrieval latency | **104 ms** | Ranking ~390 memories |
| Avg end-to-end latency | **108 ms** | Offline engine; an LLM adds its own latency |
| Cost | **$0.00** | Offline engine |
| Database | **2.2 MiB, ~2,270 rows** | Growth measured per run — see below |

### Database growth

Every run records the database's size on disk, its SQLite page count, and the
row count of every table, before and after. The report in
`evaluation/results/latest.md` names what the run added and to which tables.

Page count is recorded alongside file size because the two disagree in a way
that matters: a query run can add fifty rows and grow the file by **zero
bytes**, because SQLite reuses free pages before it asks the filesystem for
more. File size alone would report no growth at all and be wrong.

Growth is dominated by ingestion, not by querying. The 500-record corpus
produces roughly 390 memories, 500 extraction runs and 580 memory events in
about 2.2 MiB — about 4.5 KiB per dictation, with the full audit trail and raw
model responses included.

### With an LLM answering

The same suite against Groq (`openai/gpt-oss-120b`) scores **51 / 52 (98.1%)**,
recovering `eval_008` — the model ranks equally-true preferences better than
the lexical engine does. It costs **$0.0037** for the suite and raises average
end-to-end latency from 108 ms to **1,946 ms**. That run is kept at
`evaluation/results/run_20260901T061517Z.json`.

`latest.json` is deliberately the **offline** run: it is the one a reviewer
reproduces byte-for-byte with no API key, no quota and no network.

By category: abstention 9/9 · conflict 2/3 · correction 6/6 · duplicate 1/1 ·
fact 6/6 · irrelevant 7/7 · memory_update 1/1 · multi_transcript 4/4 ·
preference 4/5 · provenance 3/3 · retrieval 7/7.

### The two failures

Left in deliberately, and reproducible.

**`eval_008` — "How do I prefer my meeting summaries?"** Kivi returns two true
preferences about summaries, but not the specific "bullet points" one the case
asks for. A ranking failure among several equally true, non-contradictory
preferences. Nothing wrong is said; the most relevant thing is not said.

**`eval_402` — "When is the Atlas pricing sign-off with Sarah?"** Two live times
exist and Kivi answers with one instead of flagging the disagreement. The two
dictations phrase the appointment differently enough that the topic-overlap test
does not cluster them, so the conflict is never detected. This is the clearest
limitation of a lexical engine: it compares words, not meaning.

### Held-out extraction: does it generalise?

The pipeline score above is measured on `data/development_corpus.jsonl`, which
was written alongside the extractor. It therefore partly measures fit. Since the
assignment ends with the system being run on someone else's 500 dictations, the
more useful number is how extraction behaves on phrasing it has never seen.

`evaluation/heldout_extraction.jsonl` is 40 hand-labelled dictations in a
deliberately different register — fragments, imperatives, abbreviations, bare
product names, no `Project X` pattern, nothing sharing a template with the
corpus:

```text
"Call with Dev pushed, now Thursday 3."          -> event
"Keep the changelog dry, no exclamation marks."  -> preference
"Anya owns billing now, not Kai."                -> fact
"Right, where was I."                            -> nothing
```

```bash
python evaluation/run_extraction_eval.py
```

Recall per memory type, both engines, on the same 40 records:

| | offline | Groq (`gpt-oss-120b`) |
| --- | ---: | ---: |
| fact | **33%** | 100% |
| preference | **33%** | 100% |
| event | 88% | 100% |
| task | 71% | 100% |
| episode | 80% | 80% |
| **overall recall** | **62%** | **97%** |
| overall precision | 71% | 97% |
| records exactly right | 27/40 | 39/40 |
| filler correctly ignored | 9/10 | 10/10 |

**Read the first column as the honest cost of the offline engine.** On the
corpus it was built beside it scores 96.2%; on unfamiliar phrasing it loses a
third of the facts and two thirds of the preferences, mostly by filing them as
generic episodes — which is why `episode` precision falls to 40%. Widening the
rules catches whichever phrasing you just looked at, not the next one.

**So: run the pipeline with a real model if you are pointing it at your own
corpus.** The offline engine exists so the whole system is reproducible with no
key, no network and no cost — not because it is the better extractor. Both runs
are committed: `evaluation/results/heldout_extraction_heuristic.json` and
`…_groq.json`.

This suite reports rather than fails a build. Gating on it would invite tuning
to the held-out set, which would destroy the only thing that makes it worth
measuring.

---

### The offline engine on unfamiliar phrasing

The 0% false-abstention figure is measured against this repository's own corpus,
and that corpus was written alongside the extractor. On a corpus phrased
differently the offline engine abstains on questions it holds the answer to.

A reproduction, from a foreign corpus containing "Keep my release notes plain,
with no marketing language":

```text
Q: How should my release notes read?
   offline engine : "I don't have anything about read in your history."   WRONG
   LLM engine     : "You should keep your release notes plain and avoid
                     any marketing language."                             correct
```

Retrieval is not at fault — the preference ranks first, at 1.329, well clear of
everything else. The offline answerer's support check requires the question's
own vocabulary to appear in the memory, and "read" does not appear in a memory
phrased with "keep". The lexical engine compares words, not meaning; this is the
same root cause as `eval_402`.

**What this means for a reviewer.** Run the pipeline with a real model
configured and this class of failure largely disappears — the offline engine
exists so the whole system is reproducible with no key, no network and no cost,
not because it is the better answerer. Both are measured: the offline run is
`evaluation/results/latest.json`, and a Groq run of the same suite scores 51/52
against the offline 50/52.

---

## Where AI is used

| Stage | Used for | Default here |
| --- | --- | --- |
| Memory extraction | Deciding what to remember and what to ignore; typing, subject, slot, confidence | rules (LLM optional, at `low` effort — it is a classification-shaped task over 500 records) |
| Correction resolution | new / duplicate / supersede / conflict | rules (LLM optional, `low` effort) |
| Answer generation | Grounded answer, abstention, conflict reporting | rules (LLM optional, `medium` effort) |
| Embeddings | Semantic similarity | hashed n-grams (OpenAI / Gemini / sentence-transformers optional) |
| Corpus generation | The 500 development records | deterministic templates, no LLM — reproducibility matters more than variety |

**Not used for:** deciding whether an answer is supported (a vocabulary check,
not a model judgement), grading the evaluation (explicit assertions, no LLM
judge — an LLM grading an LLM would make the numbers unfalsifiable), or anything
in the retrieval ranking.

The whole history is never sent to a model. Retrieval selects at most 8
memories, which is the entire point: persistence, selection and provenance are
the system's job, and the model only sees what was selected.

---

## Limitations

Known and honest.

- **The default engine is lexical, not semantic.** It matches words and sentence
  shapes. Paraphrase without shared vocabulary is missed — the cause of both
  evaluation failures. Switching to an LLM addresses this; the interface is
  built for it.
- **Preference ranking is weak** when several true preferences compete. A draft
  may cite a real preference that is not the most relevant one.
- **Entity resolution is naive.** People and projects are recognised by
  capitalisation and a `Project X` pattern. Two colleagues sharing a first name
  would merge; a product name that reads like a person can be misread as one.
  There is no identity resolution layer.
- **Retrieval is O(n) per query.** BM25 is rebuilt and every vector compared on
  each question — a few milliseconds for a single user's history, but it would
  need an index and a cache at a much larger scale. Deliberate: correctness and
  inspectability over premature optimisation.
- **Single user.** `user_id` is threaded through the schema and every query, but
  there is no authentication. The assignment did not need it.
- **Time resolution is English-only and relative to the dictation timestamp.**
  "Friday at 4 PM" resolves; "the Friday after next" does not.
- **The evaluation suite is not held out.** See
  [How to read these numbers](#how-to-read-these-numbers).
- **Extraction misfires are only partly recoverable.** A dictation that produced
  no memory is reachable through
  [the rescue path](#the-rescue-when-nothing-was-learned), but a rescued answer
  is weaker than a memory: nothing has reconciled it, so it carries a caveat and
  a confidence of 0.45. Ten dictations in this corpus depend on it — six of them
  stated preferences that should have been learned outright. The rescue keeps
  the content answerable; it does not make the extraction right.
- **The rescue cannot search by time or application.** It ranks dictations by
  their words, so *"the dictation I did around 5 PM yesterday in Slack"* still
  has no path. Adding the metadata filters is small; it was left out because no
  chosen use case required it.

---

## Repository map

```
kivi-semantic-memory/
├── backend/
│   ├── api/            transcripts · memories · hey-kivi · system/evaluation
│   ├── database/       SQLite connection, migrations, vector packing
│   ├── llm/
│   │   ├── engine.py       ReasoningEngine: extract / resolve / answer
│   │   ├── heuristic.py    the offline engine (default)
│   │   ├── providers.py    Anthropic · OpenAI · Gemini
│   │   ├── prompts.py      prompts + JSON schemas for the three decisions
│   │   └── embeddings.py   hashing (default) · openai · gemini · sentence-transformers
│   ├── memory/
│   │   ├── extractor.py    transcript → memories, with reconciliation
│   │   ├── retriever.py    four-signal hybrid ranking
│   │   ├── query.py        question → intent, entities, residual/discriminative
│   │   ├── heykivi.py      ask() + support verification + provenance
│   │   ├── store.py        every read and write
│   │   └── text.py         tokenisation and vocabularies
│   ├── models/         Pydantic request/response models
│   ├── config.py       settings + model price table
│   └── main.py         FastAPI app
├── frontend/src/
│   ├── pages/          History · HeyKivi · Knowledge · Inspector
│   ├── styles/kivi.css Kivi's design tokens
│   └── services/api.js
├── data/development_corpus.jsonl      500 records
├── evaluation/
│   ├── cases.jsonl     52 cases
│   ├── run_eval.py
│   └── results/        committed results from the reference run
├── scripts/            generate_corpus · migrate · import_corpus ·
│                       process_corpus · seed · reset
├── migrations/001_initial.sql
├── docs/CORPUS_FORMAT.md
├── PRODUCT_POSITIONING.md    ← Part One (author's own writing)
├── PRODUCT_VISION.md         ← Part One (author's own writing)
├── RUN.md                    ← start here
└── .env.example
```

---

## Part One

`PRODUCT_POSITIONING.md` and `PRODUCT_VISION.md` are the assignment's Part One.
The assignment requires them to be the author's own thinking, written without
generative AI, so those files contain the structure, constraints and questions
to answer — the prose is the author's to write.
