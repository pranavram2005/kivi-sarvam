-- ===========================================================================
-- Kivi Semantic Memory - initial schema (SQLite)
--
-- Design notes
--  * `transcripts` is the immutable record of what the user actually said.
--    Nothing in this table is ever rewritten; it is the root of all provenance.
--  * `memories` holds durable understanding derived from transcripts. Every row
--    carries `source_transcript_id`, so any memory can be traced back to the
--    exact dictation that produced it.
--  * Memories are never hard-deleted by the pipeline. They move between
--    statuses (ACTIVE / SUPERSEDED / DELETED / REJECTED) so that the history of
--    what Kivi believed, and when it stopped believing it, stays inspectable.
--  * `memory_events` is an append-only audit log: why a memory was created,
--    superseded, edited or forgotten.
--  * `query_logs` records every Hey Kivi turn with its retrieval set, its used
--    set, latencies, token usage and cost - this is what Screen 4 renders.
-- ===========================================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transcripts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             TEXT    NOT NULL,
    external_id         TEXT,                    -- id from the imported corpus
    raw_asr             TEXT    NOT NULL,        -- unpolished speech recogniser output
    formatted_text      TEXT    NOT NULL,        -- what Kivi actually typed
    application         TEXT,                    -- Slack / Notes / Mail / ...
    timestamp           TEXT    NOT NULL,        -- ISO-8601, when it was dictated
    metadata            TEXT    NOT NULL DEFAULT '{}',
    processed_at        TEXT,                    -- NULL until memory extraction runs
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, external_id)
);

CREATE INDEX IF NOT EXISTS idx_transcripts_user_time
    ON transcripts (user_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_transcripts_unprocessed
    ON transcripts (user_id, processed_at);

-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memories (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             TEXT    NOT NULL,

    -- What kind of understanding this is.
    --   fact       - relatively stable  ("Rahul leads Project Atlas")
    --   preference - how the user likes things done
    --   episode    - something that happened / was said at a point in time
    --   task       - a commitment the user made
    --   event      - a scheduled thing (meeting) with a time
    type                TEXT    NOT NULL,

    content             TEXT    NOT NULL,        -- user-facing sentence
    subject             TEXT,                    -- primary entity, e.g. "Rahul"
    attribute           TEXT,                    -- slot, e.g. "meeting_time"
    value               TEXT,                    -- slot value, e.g. "Friday 4 PM"

    entities            TEXT    NOT NULL DEFAULT '[]',  -- JSON array of names
    tags                TEXT    NOT NULL DEFAULT '[]',  -- JSON array

    confidence          REAL    NOT NULL DEFAULT 0.5,
    status              TEXT    NOT NULL DEFAULT 'ACTIVE',
        -- ACTIVE | SUPERSEDED | DELETED | REJECTED

    source_transcript_id INTEGER REFERENCES transcripts (id) ON DELETE CASCADE,
    superseded_by_id    INTEGER REFERENCES memories (id) ON DELETE SET NULL,

    occurred_at         TEXT,                    -- when the remembered thing happens
    embedding           BLOB,                    -- float32 vector
    embedding_model     TEXT,

    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_memories_user_status
    ON memories (user_id, status);
CREATE INDEX IF NOT EXISTS idx_memories_subject
    ON memories (user_id, subject);
CREATE INDEX IF NOT EXISTS idx_memories_source
    ON memories (source_transcript_id);
CREATE INDEX IF NOT EXISTS idx_memories_slot
    ON memories (user_id, subject, attribute, status);

-- --------------------------------------------------------------------------
-- Typed links between memories: what superseded what, what contradicts what,
-- what duplicates what. This is the graph a reviewer walks to understand how
-- Kivi's beliefs changed over time.
CREATE TABLE IF NOT EXISTS memory_relations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id           INTEGER NOT NULL REFERENCES memories (id) ON DELETE CASCADE,
    related_memory_id   INTEGER NOT NULL REFERENCES memories (id) ON DELETE CASCADE,
    relation_type       TEXT    NOT NULL,   -- SUPERSEDES | CONTRADICTS | DUPLICATE_OF | SUPPORTS
    note                TEXT,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_relations_memory
    ON memory_relations (memory_id);
CREATE INDEX IF NOT EXISTS idx_relations_related
    ON memory_relations (related_memory_id);

-- --------------------------------------------------------------------------
-- Append-only audit trail. Answers "why does this memory look like this?"
CREATE TABLE IF NOT EXISTS memory_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id           INTEGER REFERENCES memories (id) ON DELETE CASCADE,
    transcript_id       INTEGER REFERENCES transcripts (id) ON DELETE SET NULL,
    event               TEXT    NOT NULL,   -- CREATED | SUPERSEDED | EDITED | FORGOTTEN
                                            -- | REJECTED | DUPLICATE_SKIPPED | REINSTATED
    reason              TEXT,               -- human-readable rationale
    detail              TEXT    NOT NULL DEFAULT '{}',
    actor               TEXT    NOT NULL DEFAULT 'system',  -- system | user
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_memory_events_memory
    ON memory_events (memory_id, created_at);

-- --------------------------------------------------------------------------
-- One row per memory-extraction pass over a transcript.
CREATE TABLE IF NOT EXISTS extraction_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    transcript_id       INTEGER NOT NULL REFERENCES transcripts (id) ON DELETE CASCADE,
    provider            TEXT    NOT NULL,
    model               TEXT    NOT NULL,
    decision            TEXT    NOT NULL,   -- REMEMBER | IGNORE
    rationale           TEXT,
    memories_created    INTEGER NOT NULL DEFAULT 0,
    memories_rejected   INTEGER NOT NULL DEFAULT 0,
    memories_superseded INTEGER NOT NULL DEFAULT 0,
    memories_duplicate  INTEGER NOT NULL DEFAULT 0,
    raw_response        TEXT,
    input_tokens        INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    latency_ms          REAL    NOT NULL DEFAULT 0,
    cost_usd            REAL    NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_extraction_transcript
    ON extraction_runs (transcript_id);

-- --------------------------------------------------------------------------
-- One row per Hey Kivi turn. Everything Screen 4 needs lives here.
CREATE TABLE IF NOT EXISTS query_logs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             TEXT    NOT NULL,
    question            TEXT    NOT NULL,
    answer              TEXT    NOT NULL,

    abstained           INTEGER NOT NULL DEFAULT 0,
    conflict            INTEGER NOT NULL DEFAULT 0,
    supported           INTEGER NOT NULL DEFAULT 1,
    confidence          REAL    NOT NULL DEFAULT 0,
    reasoning           TEXT,

    retrieved_memory_ids TEXT   NOT NULL DEFAULT '[]',  -- JSON, ranked
    used_memory_ids     TEXT    NOT NULL DEFAULT '[]',  -- JSON, subset actually cited
    retrieval_detail    TEXT    NOT NULL DEFAULT '[]',  -- JSON, per-candidate scores

    provider            TEXT,
    model               TEXT,
    retrieval_latency_ms REAL   NOT NULL DEFAULT 0,
    llm_latency_ms      REAL    NOT NULL DEFAULT 0,
    total_latency_ms    REAL    NOT NULL DEFAULT 0,
    input_tokens        INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    cost_usd            REAL    NOT NULL DEFAULT 0,

    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_query_logs_user
    ON query_logs (user_id, created_at DESC);

-- --------------------------------------------------------------------------
-- Persisted evaluation runs, so the Inspector screen can show the last result
-- without re-running the suite.
CREATE TABLE IF NOT EXISTS eval_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at          TEXT    NOT NULL,
    finished_at         TEXT,
    provider            TEXT,
    model               TEXT,
    embedding_provider  TEXT,
    total_cases         INTEGER NOT NULL DEFAULT 0,
    metrics             TEXT    NOT NULL DEFAULT '{}',  -- JSON
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS eval_results (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              INTEGER NOT NULL REFERENCES eval_runs (id) ON DELETE CASCADE,
    case_id             TEXT    NOT NULL,
    category            TEXT    NOT NULL,
    passed              INTEGER NOT NULL DEFAULT 0,
    detail              TEXT    NOT NULL DEFAULT '{}'   -- JSON, the full record
);

CREATE INDEX IF NOT EXISTS idx_eval_results_run
    ON eval_results (run_id);

-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_version (
    version             INTEGER PRIMARY KEY,
    applied_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO schema_version (version) VALUES (1);
