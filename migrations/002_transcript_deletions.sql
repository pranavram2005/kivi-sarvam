-- Deleting a dictation.
--
-- A person who dictated something into the wrong window, or said something they
-- would rather Kivi did not keep, needs a way to remove it. The assignment asks
-- how the person "remains in control without becoming the administrator of the
-- system", and a memory store with no delete fails that.
--
-- WHY A TABLE RATHER THAN A COLUMN
-- Every migration in `migrations/` is re-executed on every startup - init_db
-- calls executescript over all of them in filename order - so the file has to
-- be safe to run twice. `ALTER TABLE ... ADD COLUMN` is not: it raises
-- "duplicate column name" the second time. `CREATE TABLE IF NOT EXISTS` is.
--
-- WHY NOT JUST DELETE THE ROW
-- `memories.source_transcript_id` is ON DELETE CASCADE, so removing a
-- transcript would take its memories and their audit events with it. Provenance
-- is the property the whole system is built to guarantee - every answer can be
-- traced to the words that produced it - and a hard delete would leave answers
-- in the query log pointing at rows that no longer exist. So a deleted
-- dictation is hidden, not destroyed, exactly as a forgotten memory is kept
-- with status DELETED rather than removed.

CREATE TABLE IF NOT EXISTS transcript_deletions (
    transcript_id       INTEGER PRIMARY KEY
                        REFERENCES transcripts (id) ON DELETE CASCADE,
    deleted_at          TEXT NOT NULL DEFAULT (datetime('now')),
    reason              TEXT
);

INSERT OR IGNORE INTO schema_version (version) VALUES (2);
