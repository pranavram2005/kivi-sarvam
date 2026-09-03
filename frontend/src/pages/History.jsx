/**
 * Screen 1 — History.
 *
 * The user's dictations, grouped by day. Opening one shows the raw speech
 * recogniser output next to what Kivi typed, and — the part that matters — what
 * Kivi decided to remember from it, or why it decided to remember nothing.
 *
 * This is the top of the provenance chain: everything on the other screens can
 * be traced back to a line on this one.
 */

import { useEffect, useMemo, useState } from "react";
import { api, formatTime } from "../services/api";
import { Empty, ErrorBanner, PageHead, Pill, Spinner, StatusPill, TypePill } from "../components/ui";
import { Figure, Figures, StatsStrip } from "../components/PageStats";
import { AreaTrend, BarList } from "../components/charts";

export default function History({ onRefresh }) {
  const [days, setDays] = useState([]);
  const [applications, setApplications] = useState([]);
  const [search, setSearch] = useState("");
  const [application, setApplication] = useState("");
  const [openId, setOpenId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [draft, setDraft] = useState("");
  const [dictating, setDictating] = useState(false);

  // The id being deleted, and the last one deleted, so the undo offer can
  // stay on screen after the row itself has gone from the feed.
  const [removing, setRemoving] = useState(null);
  const [undo, setUndo] = useState(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [feed, apps] = await Promise.all([
        api.feed({ limit: 250, search: search || undefined, application: application || undefined }),
        api.applications(),
      ]);
      setDays(feed);
      setApplications(apps);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const timer = setTimeout(load, search ? 260 : 0);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, application]);

  async function toggle(id) {
    if (openId === id) {
      setOpenId(null);
      setDetail(null);
      return;
    }
    setOpenId(id);
    setDetail(null);
    try {
      setDetail(await api.transcript(id));
    } catch (err) {
      setError(err);
    }
  }

  async function removeDictation(item) {
    setRemoving(item.id);
    setError(null);
    try {
      const result = await api.deleteTranscript(item.id);
      setOpenId(null);
      setDetail(null);
      setUndo({ id: item.id, text: item.formatted_text, forgotten: result.memories_forgotten || [] });
      await load();
      onRefresh?.();
    } catch (err) {
      setError(err);
    } finally {
      setRemoving(null);
    }
  }

  async function undoDelete() {
    if (!undo) return;
    const id = undo.id;
    setRemoving(id);
    try {
      await api.restoreTranscript(id);
      setUndo(null);
      await load();
      onRefresh?.();
    } catch (err) {
      setError(err);
    } finally {
      setRemoving(null);
    }
  }

  async function dictate(event) {
    event.preventDefault();
    if (!draft.trim()) return;
    setDictating(true);
    try {
      const created = await api.addTranscript({
        raw_asr: draft.trim().toLowerCase().replace(/[.,!?]/g, ""),
        formatted_text: draft.trim(),
        timestamp: new Date().toISOString(),
        application: "Notes",
      });
      setDraft("");
      await load();
      onRefresh?.();
      setOpenId(created.id);
      setDetail(created);
    } catch (err) {
      setError(err);
    } finally {
      setDictating(false);
    }
  }

  const total = useMemo(
    () => days.reduce((sum, day) => sum + day.transcripts.length, 0),
    [days],
  );

  return (
    <div className="page">
      <PageHead
        eyebrow="screen 1 · history"
        title="Everything you said"
        lede="Your dictation history, exactly as it was recorded. Open any line to see the raw speech recognition, the text Kivi produced, and what — if anything — Kivi chose to remember from it."
      />

      {/* Filters, not a form: pressing Enter here must not dictate. */}
      <div className="feed__controls">
        <input
          className="field"
          placeholder="Search your dictations…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select
          className="select"
          value={application}
          onChange={(e) => setApplication(e.target.value)}
        >
          <option value="">All apps</option>
          {applications.map((app) => (
            <option key={app} value={app}>
              {app}
            </option>
          ))}
        </select>
      </div>

      <StatsStrip
        title="What you said, and how much of it Kivi kept"
        load={api.historyAnalytics}
        render={(d) => (
          <>
            <Figures>
              <Figure label="dictations" value={d.summary.dictations.toLocaleString()} />
              <Figure label="produced a memory" value={d.summary.remembered.toLocaleString()} tone="good" />
              <Figure
                label="deliberately ignored"
                value={d.summary.ignored.toLocaleString()}
                note={`${((d.summary.ignore_rate || 0) * 100).toFixed(1)}% held nothing durable`}
              />
              <Figure
                label="memories created"
                value={d.summary.memories_created.toLocaleString()}
                note={`${d.summary.memories_per_dictation} per dictation`}
              />
              <Figure
                label="skipped as duplicates"
                value={d.summary.duplicates.toLocaleString()}
                note="already known"
              />
              {d.summary.unprocessed ? (
                <Figure label="awaiting extraction" value={d.summary.unprocessed} tone="warn" />
              ) : null}
            </Figures>

            <div className="strip__charts">
              <div>
                <div className="strip__chart-title">Said versus kept, week by week</div>
                <AreaTrend
                  points={d.weekly}
                  xKey="week"
                  yKey="memories"
                  secondaryKey="dictations"
                  label="memories kept"
                  secondaryLabel="dictations"
                  height={150}
                />
              </div>
              <div>
                <div className="strip__chart-title">Where the dictations come from</div>
                <BarList
                  rows={d.by_application}
                  valueKey="dictations"
                  formatNote={(r) => `${r.memories} memories · ${r.yield} per dictation`}
                />
              </div>
            </div>
          </>
        )}
      />

      <form className="feed__controls" onSubmit={dictate}>
        <input
          className="field"
          placeholder="Add a dictation — Kivi will decide whether to remember it…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button className="btn" type="submit" disabled={dictating || !draft.trim()}>
          {dictating ? <Spinner /> : null}
          {dictating ? "Thinking…" : "Dictate"}
        </button>
      </form>

      <ErrorBanner error={error} onRetry={load} />

      {undo ? (
        <div
          className="row"
          style={{
            justifyContent: "space-between",
            alignItems: "center",
            gap: 12,
            border: "1px solid var(--edge)",
            background: "var(--surface-2)",
            borderRadius: 10,
            padding: "10px 13px",
            marginBottom: 13,
          }}
        >
          <span className="small">
            Deleted “{(undo.text || "").slice(0, 58)}
            {(undo.text || "").length > 58 ? "…" : ""}”
            {undo.forgotten.length
              ? ` — ${undo.forgotten.length} memor${
                  undo.forgotten.length === 1 ? "y" : "ies"
                } forgotten with it.`
              : "."}
          </span>
          <span className="row" style={{ gap: 8, flexShrink: 0 }}>
            <button
              className="btn btn--quiet btn--small"
              disabled={removing === undo.id}
              onClick={undoDelete}
            >
              {removing === undo.id ? "Restoring…" : "Undo"}
            </button>
            <button className="btn btn--quiet btn--small" onClick={() => setUndo(null)}>
              Dismiss
            </button>
          </span>
        </div>
      ) : null}

      {loading ? (
        <div className="row muted" style={{ padding: "26px 0" }}>
          <Spinner /> Loading your history…
        </div>
      ) : total === 0 ? (
        <Empty title="No dictations yet">
          Import a corpus with <code>python scripts/seed.py</code>, or type something above.
        </Empty>
      ) : (
        days.map((day) => (
          <section className="day" key={day.date}>
            <div className="day__head">
              <h2 className="day__label">{day.label}</h2>
              <span className="day__date">{day.date}</span>
              <span className="day__date" style={{ marginLeft: "auto" }}>
                {day.transcripts.length} dictation{day.transcripts.length === 1 ? "" : "s"}
              </span>
            </div>

            {day.transcripts.map((item) => (
              <article
                key={item.id}
                className={`dictation${openId === item.id ? " dictation--open" : ""}`}
                onClick={() => toggle(item.id)}
              >
                <div className="dictation__time">{formatTime(item.timestamp)}</div>
                <div>
                  <p className="dictation__text">{item.formatted_text}</p>
                  <div className="dictation__meta">
                    {item.application ? <Pill>{item.application}</Pill> : null}
                    {item.extraction_decision === "IGNORE" ? (
                      <Pill tone="muted">Nothing to remember</Pill>
                    ) : item.memory_count > 0 ? (
                      <Pill tone="good">
                        {item.memory_count} memor{item.memory_count === 1 ? "y" : "ies"}
                      </Pill>
                    ) : item.processed_at ? (
                      <Pill tone="muted">No memory kept</Pill>
                    ) : (
                      <Pill tone="warn">Not processed</Pill>
                    )}
                    <Pill mono tone="muted">
                      #{item.id}
                    </Pill>
                  </div>

                  {openId === item.id ? (
                    <div className="dictation__detail" onClick={(e) => e.stopPropagation()}>
                      {!detail ? (
                        <div className="row muted small">
                          <Spinner /> Loading…
                        </div>
                      ) : (
                        <>
                          <div>
                            <div className="page__eyebrow" style={{ marginBottom: 7 }}>
                              Raw speech recognition
                            </div>
                            <div className="asr">{detail.raw_asr}</div>
                          </div>

                          <div>
                            <div className="page__eyebrow" style={{ marginBottom: 7 }}>
                              What Kivi learned
                            </div>
                            {detail.extraction ? (
                              <p className="small muted" style={{ marginBottom: 9 }}>
                                {detail.extraction.decision === "IGNORE" ? "Ignored" : "Remembered"}
                                {" — "}
                                {detail.extraction.rationale}
                              </p>
                            ) : null}

                            {detail.memories?.length ? (
                              <div className="learned">
                                {detail.memories.map((memory) => (
                                  <div
                                    key={memory.id}
                                    className={
                                      "learned__item" +
                                      (memory.status === "REJECTED"
                                        ? " learned__item--rejected"
                                        : memory.status === "SUPERSEDED"
                                          ? " learned__item--superseded"
                                          : "")
                                    }
                                  >
                                    <span style={{ flex: 1 }}>{memory.content}</span>
                                    <span
                                      className="row"
                                      style={{ gap: 6, flexShrink: 0 }}
                                    >
                                      <TypePill type={memory.type} />
                                      <StatusPill status={memory.status} />
                                    </span>
                                  </div>
                                ))}
                              </div>
                            ) : (
                              <p className="small muted">
                                Nothing durable came out of this dictation.
                              </p>
                            )}
                          </div>

                          <div
                            className="row"
                            style={{
                              justifyContent: "space-between",
                              alignItems: "center",
                              gap: 10,
                              borderTop: "1px solid var(--edge)",
                              paddingTop: 11,
                            }}
                          >
                            <span className="small muted">
                              Deleting hides this dictation and forgets what it taught
                              Kivi. It can be undone.
                            </span>
                            <button
                              className="btn btn--quiet btn--small btn--danger"
                              disabled={removing === item.id}
                              onClick={() => removeDictation(item)}
                            >
                              {removing === item.id ? "Deleting…" : "Delete dictation"}
                            </button>
                          </div>
                        </>
                      )}
                    </div>
                  ) : null}
                </div>
              </article>
            ))}
          </section>
        ))
      )}
    </div>
  );
}
