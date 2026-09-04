# RUN.md — how to run, import, inspect, evaluate and reset

> **Primary review method: a completely local application — React + Vite
> frontend, FastAPI + Python backend, SQLite database.**
>
> **No API key is required.** The system ships with a deterministic offline
> reasoning engine and a dependency-free local embedder, so the entire pipeline
> — extraction, corrections, retrieval, provenance, abstention and the full
> evaluation suite — runs with an empty `.env`, no network, and no cost.
> Switching to Claude, GPT or Gemini is one line in `.env` (see
> [Using a real LLM](#7-optional-using-a-real-llm)).

> **There is also a hosted instance**, for trying it without installing
> anything: **`<PASTE YOUR RAILWAY URL HERE>`** — no credentials, no sign-in.
> It is a convenience, not the review method: the local path above is the one
> that is verified reproducible, and a hosted container can be asleep, rate
> limited, or restarted at an inconvenient moment. Everything the assignment
> asks a hosted application to document is in
> [§13 The hosted instance](#13-the-hosted-instance).

Nothing below requires contacting the author.

---

## 1. Requirements

| Tool | Version | Check with |
| --- | --- | --- |
| Python | 3.11 or newer (developed on 3.13) | `python --version` |
| Node.js | 18 or newer (developed on 24) | `node --version` |
| npm | 9 or newer | `npm --version` |

No database server is needed — SQLite ships with Python.

---

## 2. Install

All commands are run **from the repository root** unless stated otherwise.

### Backend

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### Environment

```bash
# Windows
copy .env.example .env
# macOS / Linux
cp .env.example .env
```

**You do not need to edit `.env`.** Every setting has a working default. The
file is documented if you want to change the model, the embedder or the
retrieval weights.

### Frontend

```bash
cd frontend
npm install
cd ..
```

---

## 3. Create the database and load the corpus

One command does the migration, the import and the memory extraction:

```bash
python scripts/seed.py
```

Expected output (about a second for 500 records on a laptop):

```
Kivi Semantic Memory - seeding
  database   : .../data/kivi.db
  llm        : heuristic (heuristic)
  embeddings : hashing (kivi-hash-v1)

Importing 500 record(s) from development_corpus.jsonl...
Processing 500 transcript(s) with the heuristic engine.
   500/500  remembered  466  ignored   34  memories  304  superseded  25  $0.0000

Done in 1.1s (2 ms/transcript)
  transcripts    : 500
    remembered   : 466
    ignored      : 34   (nothing durable said)
  memories       : 304 created
    superseded   : 25  (corrections applied)
    duplicates   : 76  (already known, not stored again)
    conflicts    : 51  (kept both, flagged)
    rejected     : 10  (below the confidence threshold)
  memory store   : {'ACTIVE': 355, 'REJECTED': 10, 'SUPERSEDED': 25}
  by type        : {'episode': 115, 'event': 129, 'fact': 78, 'preference': 20, 'task': 13}
```

These counts are deterministic: the corpus is fixed and the offline engine has
no randomness, so the same numbers appear on every machine. They change only if
you configure a real model, which extracts differently.

<details>
<summary>Prefer the individual steps?</summary>

```bash
python scripts/migrate.py                                   # create the schema
python scripts/import_corpus.py data/development_corpus.jsonl
python scripts/process_corpus.py                            # run memory extraction
```

</details>

---

## 4. Start the application

Two processes, in two terminals.

**Terminal 1 — backend**

```bash
uvicorn backend.main:app --reload --reload-dir backend
```

`--reload-dir backend` scopes the file watcher. Without it uvicorn watches the
whole repository — including `frontend/node_modules` — and restarts the API
every time you touch a frontend file, which drops in-flight requests.

Serves on <http://127.0.0.1:8000>. Interactive API docs at
<http://127.0.0.1:8000/docs>.

**Terminal 2 — frontend**

```bash
cd frontend
npm run dev
```

### Open

```
http://localhost:5173
```

The dev server proxies `/api` to the backend, so no CORS setup is needed.

---

## 5. What to try

The app opens on **Hey Kivi**. The suggested questions below the input are
generated from the memories Kivi actually holds, so they stay meaningful if you
import a different corpus.

| # | Do this | What it demonstrates |
| --- | --- | --- |
| 1 | Ask **"What do I need to prepare for Sarah?"** | Multi-transcript reasoning. The answer combines separate dictations, and every memory used is listed underneath with the date and app it came from. |
| 2 | Ask **"When is the Beacon empty-state walkthrough with Priya?"** | Corrections. Kivi answers **4 PM**. The original 3 PM dictation was superseded three days later. |
| 3 | Ask **"When is Rahul's birthday?"** | Abstention. Kivi says it does not have that, and offers what it *does* know about Rahul instead of guessing. |
| 4 | Ask **"When is my meeting with Tom about the Forge partner contract?"** | Conflict handling. Several live times exist with no correction between them; Kivi gives them all and says it is unsure rather than picking one. |
| 5 | Ask **"Draft a short message to Sarah about the meeting."** | Preferences. The draft is short, and names the stored preference it followed. (Preference *selection* is the weakest part of the offline engine — see Limitations in the README.) |
| 6 | Ask **"Why do you think I owe Sarah the churn assumptions?"** | Provenance. The answer quotes the original dictation and names the memory and transcript ids. |
| 7 | Click **Show working** under any answer | The retrieval ranking with per-signal scores, and which memories were used vs. merely retrieved. |
| 8 | Go to **History**, open the entry *"Actually, move the Beacon empty-state walkthrough with Priya to Thursday at 4 PM."* | Shows the raw ASR, the formatted text, and the memory it produced. |
| 9 | In **History**, type a new dictation and press Dictate | Live ingestion. Try *"Hmm, okay, give me a second."* — Kivi stores the transcript and records that it deliberately learned nothing. |
| 10 | Go to **What Kivi Knows** | Kivi's understanding grouped by people, projects, upcoming, commitments and preferences. Use **Correct** and **Forget** on any memory. |
| 11 | On that screen open **Replaced & forgotten** | Nothing is deleted. Superseded and forgotten memories are kept, and can be put back. |
| 12 | Go to **Inspector** | The evaluation run with failures shown, corpus statistics, the query log, and a full provenance trace for any question you asked. |

---

## 6. Run the evaluation

```bash
python evaluation/run_eval.py
```

Runs 52 cases against the live system — real storage, real retrieval, real
model decisions, nothing stubbed. Takes about 4 seconds offline.

```bash
python evaluation/run_eval.py --category abstention   # one category
python evaluation/run_eval.py --verbose               # print every case
python evaluation/run_eval.py --no-save               # don't write results
```

> **The command exits with status 1 when any case fails, and two cases fail on
> purpose.** So a green run of this suite still returns a non-zero exit code —
> that is the intended CI behaviour, not a broken evaluation. The expected
> result is **50 of 52 passing**, with `eval_008` and `eval_402` failing; both
> are analysed in the README under "The two failures". Judge the run by the
> printed summary, not by the exit status.

### The held-out extraction suite

```bash
python evaluation/run_extraction_eval.py
```

A second, smaller suite that measures **only** extraction, against 40 dictations
phrased unlike the development corpus. It answers the question the main suite
cannot: how much of what a real user says would Kivi actually learn?

Expect **62% recall offline** and **97% with an LLM configured** — the gap is
the point, and it is why the README recommends a real model for your own corpus.
It reports rather than fails; results land in
`evaluation/results/heldout_extraction_<engine>.json`.

**Where results go**

| Path | What it is |
| --- | --- |
| `evaluation/results/latest.json` | Full machine-readable results, every case |
| `evaluation/results/latest.md` | Readable report with metrics and every failure |
| `evaluation/results/run_<timestamp>.json` | Timestamped copy of the same run |
| Inspector screen | The same run, rendered |

Committed results from the reference run are already in the repository, so the
Inspector shows real numbers before you run anything.

---

## 7. Optional: using a real LLM

The offline engine is the default so the project runs anywhere. To use a real
model, edit `.env`:

```env
KIVI_LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

Then re-run extraction and the evaluation to compare:

```bash
python scripts/process_corpus.py --reprocess-all --workers 6
python evaluation/run_eval.py
```

`--workers` runs that many extraction calls concurrently. Only the extraction
call is parallelised - memories are still reconciled and stored strictly oldest
first, so the stored result is identical either way. It turns a ~20 minute pass
over 500 records into a few minutes against a remote model, and does nothing
for the offline engine.

| Provider | `KIVI_LLM_PROVIDER` | Key | Default model | Extra install |
| --- | --- | --- | --- | --- |
| Offline (default) | `heuristic` | none | — | none |
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` | `claude-opus-5` | already in requirements |
| Groq | `groq` | `GROQ_API_KEY` | `openai/gpt-oss-120b` | `pip install groq` |
| OpenAI | `openai` | `OPENAI_API_KEY` | `gpt-4o-mini` | `pip install openai` |
| Google | `gemini` | `GOOGLE_API_KEY` | `gemini-flash-lite-latest` | `pip install google-genai` |

Embeddings are configured separately with `KIVI_EMBEDDING_PROVIDER`
(`hashing` by default, or `openai` / `gemini` / `sentence-transformers`).

If a provider is configured but unavailable — missing key, no network, a rate
limit — the system prints a warning and falls back to the offline engine rather
than failing. Cost and token counts are reported per call in the Inspector.

**What a full LLM pass over 500 records actually costs.** Measured, not
estimated: extraction averages **1,108 input + 747 output tokens per dictation**,
so the corpus is roughly **930,000 tokens**. On Groq's free tier (8,000
tokens/minute, 200,000/day) that is about **two hours of rate-limited waiting**
and more than the daily allowance — the run will not finish in one sitting, and
you will see it fall back to the offline engine partway through. Use a paid tier
or a provider without those limits if you want the whole corpus processed by a
model; otherwise reprocess a slice:

```bash
python scripts/process_corpus.py --reprocess-all --limit 50 --workers 6
```

The offline engine is the default precisely so that none of this is on the
critical path for reviewing the system.

---

## 8. Import a different corpus (reviewer corpus)

This is the path for running the system on your own ~500 dictations.

```bash
python scripts/import_corpus.py path/to/reviewer_data.jsonl --reset --process
```

- `--reset` clears existing data first. Omit it to add to what is there.
- `--process` runs memory extraction immediately. Omit it and run
  `python scripts/process_corpus.py` yourself.

**If you have configured your own model (§7), import and extract separately
instead.** `--process` extracts one record at a time, which is right for the
offline engine and slow against an API; `process_corpus.py` takes `--workers`
and the import itself is unaffected either way:

```bash
python scripts/import_corpus.py path/to/reviewer_data.jsonl --reset
python scripts/process_corpus.py --workers 6     # paid tier; see the note below
```

Both commands print progress as they go, and extraction is resumable — it
selects transcripts with no `processed_at`, so if it is interrupted, running it
again continues from where it stopped rather than starting over. Rough timings
for 500 records, measured on this corpus:

| engine | extraction |
| --- | ---: |
| offline (default) | about 1 second |
| Gemini Flash-Lite, `--workers 6`, paid tier | 15–20 minutes |
| Gemini Flash-Lite, free tier | not achievable — 15 requests/minute is below
  what a single serial worker asks for, so a large share of the corpus falls
  back to rules. The run says how many. |

The stored result does not depend on `--workers`. Only the extraction call is
parallelised; reconciliation and writing stay strictly sequential in timestamp
order, because a correction only means something once the thing it corrects has
already been learned.

> **Stop the backend first** if it is running (Ctrl+C). Windows will not let the
> database file be replaced while a process holds it open; the script tells you
> this if it happens.

Then start the app and ask questions grounded in that history. The suggested
questions, the People and Projects groupings, and the Inspector all derive from
whatever was imported — nothing is hardcoded to the development corpus.

**Record format** (JSON Lines, one object per line, or a single JSON array):

```json
{
  "id": "tr_001",
  "raw_asr": "meeting rahul friday atlas pricing",
  "formatted_output": "Meeting with Rahul on Friday about Project Atlas pricing.",
  "timestamp": "2026-08-20T09:30:00",
  "application": "Slack",
  "metadata": { "workspace": "work" }
}
```

| Field | Required | Notes |
| --- | --- | --- |
| `timestamp` | **yes** | ISO-8601. Ordering matters — corrections need to arrive after what they correct. |
| `formatted_output` | one of the two | `formatted_text` is accepted as an alias. |
| `raw_asr` | one of the two | Falls back to `formatted_output` if absent. |
| `id` | recommended | Re-importing the same id updates that record instead of duplicating it. |
| `application` | no | Shown in the feed and in answer sources. |
| `metadata` | no | Any JSON object; stored and returned unchanged. |

Records are validated before anything is written, and every problem is reported
with its line number. Full details: [`docs/CORPUS_FORMAT.md`](docs/CORPUS_FORMAT.md).

You can also drag a `.jsonl` file at `POST /api/corpus/upload` via
<http://127.0.0.1:8000/docs>.

---

## 9. Inspecting what Kivi stored

**In the app** — the Inspector screen: evaluation results, corpus statistics,
the query log, and a provenance trace from any answer back to the words that
produced it.

**Over HTTP** — <http://127.0.0.1:8000/docs>:

```
GET  /api/system/status          what is configured and what is stored
GET  /api/memories?status=ACTIVE every current memory
GET  /api/memories/{id}          one memory with its full audit trail
GET  /api/transcripts/{id}       one dictation and everything learned from it
GET  /api/hey-kivi/queries/{id}  one answer with its retrieval ranking
```

**Directly in SQLite** — `data/kivi.db` opens in any SQLite browser:

```sql
SELECT type, status, COUNT(*) FROM memories GROUP BY type, status;

-- what Kivi decided NOT to remember, and why
SELECT t.formatted_text, e.reason
FROM memory_events e JOIN transcripts t ON t.id = e.transcript_id
WHERE e.event = 'IGNORED' LIMIT 20;

-- every correction, old value to new
SELECT old.content AS was, new.content AS now_is, r.note
FROM memory_relations r
JOIN memories new ON new.id = r.memory_id
JOIN memories old ON old.id = r.related_memory_id
WHERE r.relation_type = 'SUPERSEDES';
```

---

## 10. Reset

```bash
python scripts/reset.py           # asks for confirmation
python scripts/reset.py --yes     # skips the prompt
python scripts/reset.py --yes --results   # also clears evaluation results
```

Stop the backend first. To rebuild from scratch afterwards:

```bash
python scripts/seed.py
```

To regenerate the corpus and the evaluation cases themselves (deterministic —
the same 500 records every time):

```bash
python scripts/generate_corpus.py
```

---

## 11. Full sequence, from a clean clone

```bash
git clone https://github.com/pranavram2005/kivi-sarvam.git
cd kivi-sarvam

python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -r requirements.txt
copy .env.example .env                             # no editing needed

python scripts/seed.py                             # migrate + import + extract
python evaluation/run_eval.py                      # 50/52 in ~4 seconds

uvicorn backend.main:app --reload --reload-dir backend   # terminal 1
cd frontend && npm install && npm run dev          # terminal 2
# open http://localhost:5173

python scripts/import_corpus.py reviewer_data.jsonl --reset --process
python scripts/reset.py --yes
```

---

## 12. Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `Could not reach the Kivi backend` in the UI | The backend is not running. Start `uvicorn backend.main:app --reload` from the repository root. |
| `Cannot reset ...: another process is using it` | The backend has the SQLite file open. Stop it with Ctrl+C, then re-run. |
| `ModuleNotFoundError: No module named 'backend'` | Run commands from the **repository root**, not from `backend/`. |
| `The database is empty, so there is nothing to evaluate` | Run `python scripts/seed.py` first. |
| Evaluation warns that transcripts are unprocessed | Run `python scripts/process_corpus.py`. |
| Fonts look like plain serif/sans | Petrona, Space Grotesk and Geist Mono load from CDNs. Offline, the fallback stacks are used; layout and colour are unaffected. |
| The UI shows `Could not reach the Kivi backend` intermittently while you edit files | You started uvicorn with a bare `--reload`, so it is restarting on frontend edits. Add `--reload-dir backend`. |
| Port 5173 or 8000 already in use | `uvicorn backend.main:app --port 8001`, or change `server.port` in `frontend/vite.config.js`. |
| `npm run dev` fails on an old Node | Node 18+ is required by Vite 6. Check with `node --version`. |

---

## 13. The hosted instance

Everything in this section works against the deployed URL, with no shell and no
local install. It is here because the assignment requires a hosted application
to document its URL, credentials, interactions, evaluation procedure and corpus
import — and because a reviewer should not have to take "it also runs hosted" on
trust.

**URL:** `<PASTE YOUR RAILWAY URL HERE>` — the interface and the API are on the
same origin, so every `curl` below uses this same address.

**Credentials:** none. There is no sign-in; the instance holds one demo user's
history and no real personal data.

### Interactions to try

Open the URL and work through the four screens left to right. The three that
show the system doing something a plain search cannot:

| Ask this | What to notice |
| --- | --- |
| *"When is the Project Forge deadline?"* | Answers **Wednesday**. A dictation said Thursday first; that memory is `SUPERSEDED` and does not even reach the candidate list. **Show working** proves it. |
| *"When is Kenji's birthday?"* | Refuses. The history has a great deal about Kenji and nothing about a birthday. |
| *"Draft a short message to Sarah about the Q4 forecast."* | The draft is short **because** of something dictated weeks earlier, and it cites the preference it obeyed. |

Then use **Dictate** in the Hey Kivi composer to add a dictation and watch Kivi
say what it decided to remember — or that it decided to remember nothing.

### Evaluation procedure

```bash
curl -X POST "<URL>/api/evaluation/run"
```

Runs the same 52-case suite as the local command against the live hosted state
and returns metrics, per-case results and database growth as JSON. It imports
the suite rather than reimplementing it, so the hosted and local numbers cannot
drift apart. Expect **50 of 52**, with `eval_008` and `eval_402` failing by
design — both are analysed in the README.

Add `?category=abstention` to run one category. Against the offline engine the
full suite takes a few seconds; if the instance has been switched to a real
model it takes minutes, because every case is a live model call.

The Inspector screen shows the same results without curl.

### Corpus import procedure

Upload a JSONL file in the documented format (`docs/CORPUS_FORMAT.md`):

```bash
curl -X POST "<URL>/api/corpus/upload?reset=true&process=true&engine=heuristic" \
     -F "file=@your_corpus.jsonl"
```

`reset=true` clears the demo corpus first, so the instance holds only your
records. `process=true` runs extraction, reconciliation and embedding as each
record lands. The response reports how many were remembered, ignored,
superseded, skipped as duplicates and rejected.

`engine=heuristic` on that command controls **which engine builds the
memories**. It never affects which engine answers questions — that is always
whatever `KIVI_LLM_PROVIDER` is set to.

It is on the default command because a bulk import and a single question have
opposite requirements. One question through a model costs a couple of seconds
and is worth it. Importing 500 records is a few hundred extraction calls plus a
reconciliation call per candidate memory, run in timestamp order because a
correction only means something after the thing it corrects. Measured on this
corpus:

| | per record | 500 records |
| --- | ---: | ---: |
| `engine=heuristic` | 0.01 s | **~5 seconds** |
| a model, free tier | — | **not achievable — see below** |
| a model, paid tier (`workers=6`) | ~1–2 s | 15–20 minutes |

The model figure is a range because it depends on how much reconciling each
record sets off: a dictation that only adds a memory is one call, one that
corrects something already known is one call plus a resolution call per
candidate. Either way it is longer than a proxy will hold a connection open, so
a single request for the whole corpus would appear to hang and then fail.

#### Using your own model for extraction as well

Extraction is where a model earns its place in this system — measured on a
held-out set, recall goes from 62% offline to 97% with one. If you want the
memories built by your model rather than by rules, set `GOOGLE_API_KEY` and
`KIVI_LLM_PROVIDER=gemini` (or your provider of choice — all four are installed
in the image) in the deployment's variables, then import in two steps:

```bash
# 1. import without extracting — fast, and returns immediately
curl -X POST "<URL>/api/corpus/upload?reset=true&process=false"      -F "file=@your_corpus.jsonl"

# 2. extract with the configured model, in batches that fit inside a request
curl -X POST "<URL>/api/memory/process"      -H "Content-Type: application/json"      -d '{"limit": 100, "workers": 6}'          # paid tier; see the note below
```

Repeat step 2 until it reports `"processed": 0`.

**A free tier cannot build this corpus with a model, at any `workers` setting.**
Gemini's free tier allows **15 requests per minute per project per model**, and
one record costs one extraction call plus a reconciliation call per candidate
memory. Measured on this corpus:

| | throughput | fell back to rules |
| --- | ---: | ---: |
| `workers: 6` | far over the limit | **194 of 225 (86%)** |
| `workers: 1` | ~21 records/min | **19 of 53 (36%)** |

Even fully serial is too fast: a record takes about 2.9 seconds, which is 21
requests a minute against a ceiling of 15. Rejected calls return HTTP 429, and
the system treats that as a reason to degrade rather than to stop — the offline
engine takes over, the run completes, and the summary still names a model.
There is no `--workers` value that fixes this, because nothing paces requests to
the quota. That rate limiter is a known gap, recorded in the README's
Limitations.

**So on a free tier, use `engine=heuristic` for the import** — the default
command above — and let the model answer questions, which is one call at a time
and well inside the limit. On a paid tier the ceiling disappears and
`workers: 6` builds the corpus in fifteen to twenty minutes.

Both the CLI and the API make this visible rather than leaving it to be
inferred. `process_corpus.py` prints a warning when any record fell back:

```
  !! 194 of 225 transcript(s) (86%) were extracted by the OFFLINE engine, not gemini.
     172 of those were rate limited (HTTP 429).
```

and every affected row carries `[fell back to the offline engine: ...]` in its
rationale, visible per dictation in the Inspector.

Batching is safe rather than merely convenient: extraction selects transcripts
with no `processed_at`, so each call continues where the last stopped, and a
call that dies costs you only the records still outstanding — nothing is
processed twice and nothing is skipped. `workers` parallelises the extraction
call alone; reconciliation and writing stay sequential in timestamp order, so
the memories you end up with do not depend on it.

The response names the engine that did the work, so there is no ambiguity about
which one produced the memories you are about to inspect.

Running the whole corpus locally is faster than doing it over HTTP — see §8,
which uses the same engine with `--workers` and no request timeout to work
around.

To send records as a JSON body instead, `POST <URL>/api/corpus/import`. To put
the demo corpus back, `POST <URL>/api/system/reset` then re-import.

**The database is on a persistent volume at `/data`**, so an imported corpus
survives a restart. Seeding happens only when that volume is empty — a restart
never overwrites what you imported.

### Deploying your own copy

```bash
# 1. push the repository to GitHub
# 2. railway.com -> New Project -> Deploy from GitHub repo
# 3. Variables:  (optional) GROQ_API_KEY=...   KIVI_LLM_PROVIDER=groq
#                do NOT set KIVI_DATABASE_URL  <- see below
# 4. Volumes:    add one, mount path /data      <- required, see below
# 5. Networking: Generate Domain
```

> **Do not set `KIVI_DATABASE_URL` on the host.** The image already sets
> `sqlite:////data/kivi.db` — four slashes, an absolute path on the volume.
> The three-slash form in `.env.example` is the correct *local* value and means
> a path relative to the repository, so setting it here puts the database
> inside the container instead of on the volume: it is discarded on every
> restart, and the container reseeds each time. The entrypoint prints a
> `[kivi] WARNING: database resolves to ...` line if this happens — if you see
> it in the deploy logs, delete the variable and redeploy.

`railway.toml` pins the Dockerfile builder and a `/api/health` healthcheck. The
Dockerfile builds the frontend in a Node stage and copies the static output into
a Python image, so one container serves the interface and the API from one
origin — one process to keep alive, and no CORS.

**The volume is not optional.** SQLite is a file. Without a volume mounted at
`/data` every restart discards whatever was imported, which is the one failure a
hosted review cannot survive.

The container defaults to `KIVI_LLM_PROVIDER=heuristic`: no key needed, and the
evaluation finishes in seconds instead of minutes of rate-limited waiting.

**To use a real model on the hosted instance**, set two variables:

```
GOOGLE_API_KEY=...
KIVI_LLM_PROVIDER=gemini
```

Gemini is the provider to pick if you are on a free tier — Groq's free tier
caps at 200,000 tokens/day and a 500-record pass needs about 930,000. All four
providers are installed in the image, so no rebuild is needed to switch.

**Seeding always uses the offline engine, whatever the provider is set to.**
That is deliberate and worth knowing: seeding runs *before* uvicorn binds a
port, and 500 records is about a second offline against fifteen to twenty
minutes through an API. A healthcheck expecting the service within a few
minutes would kill the container mid-seed and the platform would restart it
into the same seed — a boot loop that never serves a request. The log line
says which engine did what:

```
[kivi] extraction complete (offline engine; serving with gemini)
```

Answering then uses the configured model. If you want the *memories* built by
a model too, reprocess once the instance is up and answering:

```bash
curl -X POST "<URL>/api/memory/process"      -H "Content-Type: application/json" -d '{"reprocess_all": true}'
```
