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

  // What the dictate flow is currently doing, and a clock so the wait is
  // legible rather than indefinite.
  const [stage, setStage] = useState(null);
  const [tick, setTick] = useState(0);

  // One real dictation and one real memory drawn from it, shown at the top of
  // the screen. An instance explains what this screen is for faster than a
  // paragraph does, and because it is taken from the live database rather
  // than written here, it cannot drift from what the system actually does.
  const [example, setExample] = useState(null);

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

      const withMemory = feed
        .flatMap((day) => day.transcripts)
        .find((t) => t.memory_count > 0);
      if (withMemory) {
        try {
          const full = await api.transcript(withMemory.id);
          const kept = (full.memories || []).find((m) => m.status === "ACTIVE");
          if (kept) setExample({ said: full.formatted_text, kept: kept.content });
        } catch {
          /* the hero is illustrative; the screen works without it */
        }
      }
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

  useEffect(() => {
    if (!stage || stage.step === "done") return undefined;
    const id = setInterval(() => setTick((t) => t + 1), 200);
    return () => clearInterval(id);
  }, [stage]);

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

  // Dictating is split into its two real steps rather than one opaque wait.
  // Storing is instant; extraction is one model call plus a resolution call per
  // candidate memory, run in sequence, which is seconds against a hosted model.
  // Showing a single spinner for all of it looks like the app has hung, so each
  // stage below corresponds to an actual request completing - nothing here is a
  // timed animation pretending to be progress.
  async function dictate(event) {
    event.preventDefault();
    if (!draft.trim()) return;
    const text = draft.trim();
    setDictating(true);
    setStage({ step: "saving", startedAt: Date.now() });
    try {
      const created = await api.addTranscript(
        {
          raw_asr: text.toLowerCase().replace(/[.,!?]/g, ""),
          formatted_text: text,
          timestamp: new Date().toISOString(),
          application: "Notes",
        },
        { process: false },
      );
      setDraft("");

      // The dictation is stored, so show it before extraction starts. It
      // appears in the feed marked "Not processed", which is the truth.
      setStage({ step: "extracting", startedAt: Date.now(), id: created.id });
      await load();
      setOpenId(created.id);
      setDetail(created);

      const result = await api.process({});
      const full = await api.transcript(created.id);
      setDetail(full);
      setStage({
        step: "done",
        startedAt: Date.now(),
        id: created.id,
        provider: result?.provider,
        model: result?.model,
        elapsedMs: result?.elapsed_ms,
        created: result?.memories_created ?? 0,
        superseded: result?.memories_superseded ?? 0,
        duplicate: result?.memories_duplicate ?? 0,
        rejected: result?.memories_rejected ?? 0,
        ignored: result?.ignored ?? 0,
        rationale: full?.extraction?.rationale,
      });
      await load();
      onRefresh?.();
    } catch (err) {
      setError(err);
      setStage(null);
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
      />

      <div className="hero">
        <p className="hero__line">
          everything you said. <span>and the little Kivi kept.</span>
        </p>

        {example ? (
          <div className="hero__pair">
            <div style={{ minWidth: 0 }}>
              <div className="hero__label">you said</div>
              <div className="hero__said">
                “{example.said.length > 96 ? example.said.slice(0, 96) + "…" : example.said}”
              </div>
            </div>

            <svg className="hero__arrow" viewBox="0 0 76 15" aria-hidden="true">
              <path d="M2 8.4c14-3.4 30-4.6 46-3.4M60 8.6c4.6.2 9 .6 13 1.2" />
              <path d="M56 1.6c5.4 3 11 5.6 17 8.2-6 1-11.6 2.4-17 4.2" />
            </svg>

            <div style={{ minWidth: 0 }}>
              <div className="hero__label">kivi kept</div>
              <div className="hero__kept">{example.kept}</div>
            </div>
          </div>
        ) : (
          <div className="hero__pair">
            <div className="small muted">
              Nothing dictated yet — say something below and Kivi will decide whether it is
              worth remembering.
            </div>
          </div>
        )}
      </div>

      <form className="addrow" onSubmit={dictate}>
        <span className="addrow__mark" aria-hidden="true">
          +
        </span>
        <input
          className="addrow__input"
          placeholder="tell kivi something"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button className="btn btn--small" type="submit" disabled={dictating || !draft.trim()}>
          {dictating ? <Spinner /> : null}
          {dictating ? "Working…" : "Dictate"}
        </button>
      </form>

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

      {stage ? (
        <div
          className="row"
          style={{
            alignItems: "flex-start",
            gap: 11,
            border: "1px solid var(--edge)",
            background: "var(--surface-2)",
            borderRadius: 10,
            padding: "11px 13px",
            marginBottom: 13,
          }}
        >
          {stage.step === "done" ? null : <Spinner />}
          <div style={{ flex: 1, minWidth: 0 }}>
            {stage.step === "saving" ? (
              <>
                <div className="small">Saving what you said…</div>
                <div className="small muted">
                  The dictation is stored before anything is inferred from it.
                </div>
              </>
            ) : stage.step === "extracting" ? (
              <>
                <div className="small">
                  Stored. Kivi is reading it…{" "}
                  <span className="mono muted">
                    {((Date.now() - stage.startedAt) / 1000).toFixed(1)}s
                  </span>
                </div>
                <div className="small muted">
                  Deciding what is worth remembering, then checking each candidate
                  against what Kivi already knows. One model call per step, so this
                  takes a few seconds against a hosted model and is instant offline.
                </div>
              </>
            ) : (
              <>
                <div className="small">
                  {stage.created + stage.superseded === 0
                    ? "Nothing durable came out of that."
                    : [
                        stage.created ? `${stage.created} remembered` : null,
                        stage.superseded ? `${stage.superseded} corrected` : null,
                        stage.duplicate ? `${stage.duplicate} already known` : null,
                        stage.rejected ? `${stage.rejected} below confidence` : null,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                </div>
                <div className="small muted">
                  {stage.rationale ? `${stage.rationale} ` : ""}
                  <span className="mono">
                    {stage.provider}
                    {stage.model && stage.model !== stage.provider ? ` · ${stage.model}` : ""}
                    {stage.elapsedMs ? ` · ${(stage.elapsedMs / 1000).toFixed(1)}s` : ""}
                  </span>
                </div>
              </>
            )}
          </div>
          {stage.step === "done" ? (
            <button className="btn btn--quiet btn--small" onClick={() => setStage(null)}>
              Dismiss
            </button>
          ) : null}
        </div>
      ) : null}

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
