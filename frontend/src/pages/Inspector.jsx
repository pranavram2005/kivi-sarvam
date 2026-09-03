/**
 * Screen 4 — Inspector.
 *
 * The technical screen, for reviewers. Everything the product screens
 * deliberately hide lives here: the evaluation run with its failures visible,
 * the memory ids, the retrieval scores, latencies, token counts and cost, and
 * the full provenance trace from an answer back to the words the user said.
 *
 * The failing cases are shown first and are not collapsible. An evaluation that
 * only displays its successes is decoration, not evidence.
 */

import { useEffect, useState } from "react";

// How many evaluation cases are shown before the section asks to be opened.
const CASE_PREVIEW = 6;
import { api, formatPercent, formatStamp } from "../services/api";
import { Empty, ErrorBanner, PageHead, Pill, Spinner } from "../components/ui";
import { Figure, Figures, StatsStrip } from "../components/PageStats";
import { BarList } from "../components/charts";

export default function Inspector({ status }) {
  const [evaluation, setEvaluation] = useState(null);
  const [queries, setQueries] = useState([]);
  const [openQuery, setOpenQuery] = useState(null);
  const [allCases, setAllCases] = useState(false);
  const [detail, setDetail] = useState(null);
  const [filter, setFilter] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  async function load() {
    setLoading(true);
    try {
      const [ev, qs] = await Promise.all([api.evaluation(), api.history(40)]);
      setEvaluation(ev);
      setQueries(qs);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function openTrace(id) {
    if (openQuery === id) {
      setOpenQuery(null);
      setDetail(null);
      return;
    }
    setOpenQuery(id);
    setDetail(null);
    try {
      setDetail(await api.queryDetail(id));
    } catch (err) {
      setError(err);
    }
  }

  if (loading) {
    return (
      <div className="page">
        <div className="row muted" style={{ padding: "40px 0" }}>
          <Spinner /> Loading evaluation results…
        </div>
      </div>
    );
  }

  const metrics = evaluation?.metrics || {};
  const cases = evaluation?.cases || [];
  const failures = cases.filter((c) => !c.passed);
  const shown =
    filter === "all" ? cases : filter === "failed" ? failures : cases.filter((c) => c.passed);

  // Failures first. The evaluation writes cases in suite order, which puts the
  // two that fail at positions 8 and 36 - so a six-case preview taken in that
  // order would show none of them, hiding the only cases worth opening the
  // section for. Sorting here rather than in the runner keeps the saved results
  // in the order the suite ran them.
  const ordered = [...shown].sort((a, b) => Number(a.passed) - Number(b.passed));
  const visibleCases = allCases ? ordered : ordered.slice(0, CASE_PREVIEW);

  return (
    <div className="page">
      <PageHead
        eyebrow="screen 4 · inspector"
        title="The evidence"
        lede="Everything the other screens hide on purpose. Reproduce this yourself with: python evaluation/run_eval.py"
      />

      <ErrorBanner error={error} onRetry={load} />

      <StatsStrip
        always
        title="How Kivi has answered, across every question asked here"
        load={api.queryAnalytics}
        render={(d) => {
          const s = d.summary;
          if (!s.total) {
            return (
              <p className="small muted">
                No questions asked yet — ask something on the Hey Kivi screen and the
                behaviour of every turn shows up here.
              </p>
            );
          }
          return (
            <>
              <Figures>
                <Figure label="questions" value={s.total} />
                <Figure label="answered from memory" value={s.grounded} tone="good" />
                <Figure
                  label="refused honestly"
                  value={s.abstained}
                  tone="warn"
                  note="said it did not know"
                />
                <Figure
                  label="conflicts surfaced"
                  value={s.conflict}
                  tone="warn"
                  note="gave both, picked neither"
                />
                <Figure
                  label="grounded"
                  value={formatPercent(s.supported_rate, 0)}
                  note="content backed by cited memories"
                />
                <Figure
                  label="avg confidence"
                  value={s.avg_confidence.toFixed(2)}
                  note="self-reported, uncalibrated"
                />
                <Figure label="avg retrieval" value={`${s.avg_retrieval_latency_ms} ms`} />
                <Figure
                  label="avg end-to-end"
                  value={`${s.avg_total_latency_ms} ms`}
                  note={s.cost_usd ? `$${s.cost_usd} spent` : "no model cost"}
                />
              </Figures>

              {d.engines.length ? (
                <div>
                  <div className="strip__chart-title">
                    Which engine answered — token counts are the tell
                  </div>
                  <DataTableLite
                    rows={d.engines.map((e) => ({
                      key: `${e.provider} · ${e.model}`,
                      count: e.questions,
                      note: `${e.tokens.toLocaleString()} tokens · $${e.cost_usd}`,
                    }))}
                  />
                </div>
              ) : null}
            </>
          );
        }}
      />

      {!evaluation || evaluation.source === "none" ? (
        <Empty title="No evaluation run yet">
          Run <code>python evaluation/run_eval.py</code> from the repository root, then reload.
        </Empty>
      ) : (
        <>
          <div className="row row--wrap" style={{ marginBottom: 20 }}>
            <Pill mono>{evaluation.run?.provider} / {evaluation.run?.model}</Pill>
            <Pill mono>embeddings: {evaluation.run?.embedding_provider}</Pill>
            <Pill mono>{evaluation.run?.started_at?.slice(0, 19).replace("T", " ")}</Pill>
            <Pill tone="muted">source: {evaluation.source}</Pill>
          </div>

          <div className="metrics">
            <Metric
              label="Cases passed"
              value={`${metrics.cases_passed}/${metrics.cases_total}`}
              ratio={metrics.pass_rate}
              note="Every expectation met."
            />
            <Metric
              label="Hallucination rate"
              value={formatPercent(metrics.hallucination_rate, 1)}
              ratio={1 - (metrics.hallucination_rate ?? 0)}
              note="Answers that were neither an abstention nor supported by their citations."
            />
            <Metric
              label="Supported answers"
              value={formatPercent(metrics.supported_answer_rate, 1)}
              ratio={metrics.supported_answer_rate}
              note="Answer content backed by the memories it cites."
            />
            <Metric
              label="Correct abstention"
              value={formatPercent(metrics.correct_abstention_rate, 1)}
              ratio={metrics.correct_abstention_rate}
              note="Questions with no supporting memory that Kivi refused to answer."
            />
            <Metric
              label="False abstention"
              value={formatPercent(metrics.false_abstention_rate, 1)}
              ratio={1 - (metrics.false_abstention_rate ?? 0)}
              note="Answerable questions Kivi wrongly refused."
            />
            <Metric
              label="Retrieval recall@k"
              value={formatPercent(metrics.retrieval_recall_at_k, 1)}
              ratio={metrics.retrieval_recall_at_k}
              note="Expected source transcript reached by retrieval."
            />
            <Metric
              label="Used-source precision"
              value={formatPercent(metrics.used_source_precision, 1)}
              ratio={metrics.used_source_precision}
              note="Expected source actually cited in the answer."
            />
            <Metric
              label="Memory updates"
              value={formatPercent(metrics.memory_update_accuracy, 1)}
              ratio={metrics.memory_update_accuracy}
              note="Corrections that superseded the value they replaced."
            />
            <Metric
              label="Conflict handling"
              value={formatPercent(metrics.conflict_handling_accuracy, 1)}
              ratio={metrics.conflict_handling_accuracy}
              note="Live disagreements surfaced rather than silently resolved."
            />
            <Metric
              label="Ignore accuracy"
              value={formatPercent(metrics.ignore_accuracy, 1)}
              ratio={metrics.ignore_accuracy}
              note="Filler that correctly produced no durable memory."
            />
            <Metric
              label="Avg retrieval"
              value={`${Math.round(metrics.avg_retrieval_latency_ms ?? 0)} ms`}
              note="Search across the whole memory store."
            />
            <Metric
              label="Avg end-to-end"
              value={`${Math.round(metrics.avg_end_to_end_latency_ms ?? 0)} ms`}
              note={`p95 ${Math.round(metrics.p95_end_to_end_latency_ms ?? 0)} ms`}
            />
          </div>

          {metrics.corpus ? <CorpusPanel corpus={metrics.corpus} /> : null}

          <section style={{ marginTop: 30 }}>
            <div className="row row--between" style={{ marginBottom: 14 }}>
              <h2 className="know-section__title">Evaluation cases</h2>
              <div className="tabs" style={{ margin: 0 }}>
                {[
                  ["all", `All ${cases.length}`],
                  ["failed", `Failed ${failures.length}`],
                  ["passed", `Passed ${cases.length - failures.length}`],
                ].map(([key, label]) => (
                  <button
                    key={key}
                    className="tab"
                    aria-selected={filter === key}
                    onClick={() => setFilter(key)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>

            <div className="stack">
              {visibleCases.map((c) => (
                <div
                  className={"card " + (c.passed ? "case--pass" : "case--fail")}
                  key={c.test_id}
                  style={{ padding: "13px 17px" }}
                >
                  <div className="row row--between row--wrap" style={{ gap: 12 }}>
                    <strong style={{ fontWeight: 550, minWidth: 0 }}>
                      {c.question || <em className="muted">state check</em>}
                    </strong>
                    <div className="row" style={{ gap: 7, flexShrink: 0 }}>
                      <Pill>{c.category}</Pill>
                      {c.passed ? <Pill tone="good">pass</Pill> : <Pill tone="rose">fail</Pill>}
                      <Pill mono>{Math.round(c.end_to_end_latency_ms || 0)} ms</Pill>
                      <Pill mono>{c.test_id}</Pill>
                    </div>
                  </div>

                  <p className="small muted" style={{ marginTop: 7 }}>
                    {(c.answer || "").slice(0, 180)}
                    {(c.answer || "").length > 180 ? "…" : ""}
                  </p>

                  {/* A failure's assertions are the reason to read the case at
                      all, so they are never behind a control. */}
                  {c.failures?.length ? (
                    <ul
                      style={{
                        margin: "9px 0 0",
                        paddingLeft: 16,
                        color: "#e0836f",
                        fontSize: 12.5,
                        lineHeight: 1.6,
                      }}
                    >
                      {c.failures.map((f, i) => (
                        <li key={i}>{f}</li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ))}
            </div>

            {/* One control for the section, not one per case. Fifty-two cards
                is a long scroll past a list most readers only want the shape
                of; the failures are always among the first shown. */}
            {shown.length > CASE_PREVIEW ? (
              <button
                className="btn btn--quiet"
                style={{ marginTop: 12, width: "100%" }}
                onClick={() => setAllCases((v) => !v)}
              >
                {allCases
                  ? `Show fewer — collapse to ${CASE_PREVIEW}`
                  : `View all ${shown.length} cases — ${shown.length - CASE_PREVIEW} more`}
              </button>
            ) : null}
          </section>
        </>
      )}

      <section style={{ marginTop: 36 }}>
        <h2 className="know-section__title" style={{ marginBottom: 6 }}>
          Query log
        </h2>
        <p className="page__lede" style={{ marginBottom: 16 }}>
          Every Hey Kivi turn from this installation. Open one to walk the provenance chain:
          answer → memories used → source transcripts → the words you actually said.
        </p>

        {!queries.length ? (
          <Empty title="No questions asked yet">Ask something on the Hey Kivi screen.</Empty>
        ) : (
          <div className="stack">
            {queries.map((q) => (
              <div className="card card--hover" key={q.id}>
                <button
                  onClick={() => openTrace(q.id)}
                  style={{
                    all: "unset",
                    display: "block",
                    width: "100%",
                    cursor: "pointer",
                    padding: "14px 18px",
                  }}
                >
                  <div className="row row--between row--wrap" style={{ gap: 12 }}>
                    <strong style={{ fontWeight: 550 }}>{q.question}</strong>
                    <div className="row" style={{ gap: 7 }}>
                      {q.abstained ? (
                        <Pill tone="warn">abstained</Pill>
                      ) : q.conflict ? (
                        <Pill tone="rose">conflict</Pill>
                      ) : q.supported ? (
                        <Pill tone="good">supported</Pill>
                      ) : (
                        <Pill tone="rose">unsupported</Pill>
                      )}
                      <Pill mono>{Math.round(q.total_latency_ms)} ms</Pill>
                      <Pill mono>#{q.id}</Pill>
                    </div>
                  </div>
                  <p className="small muted" style={{ marginTop: 7 }}>
                    {q.answer.slice(0, 190)}
                    {q.answer.length > 190 ? "…" : ""}
                  </p>
                </button>

                {openQuery === q.id ? (
                  <div style={{ padding: "0 18px 18px" }}>
                    {!detail ? (
                      <div className="row muted small">
                        <Spinner /> Loading trace…
                      </div>
                    ) : (
                      <Trace detail={detail} />
                    )}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </section>

      {status ? (
        <section style={{ marginTop: 36 }}>
          <h2 className="know-section__title" style={{ marginBottom: 14 }}>
            System
          </h2>
          <div className="table__scroll card" style={{ padding: "4px 10px" }}>
            <table className="table">
              <tbody>
                {Object.entries({
                  "reasoning engine": `${status.llm_provider} (${status.llm_model})`,
                  embeddings: `${status.embedding_provider} (${status.embedding_model}, ${status.embedding_dim}d)`,
                  database: status.database,
                  transcripts: status.transcripts,
                  "awaiting extraction": status.transcripts_unprocessed,
                  "memories by status": JSON.stringify(status.memories),
                  "memories by type": JSON.stringify(status.memory_types),
                  "questions asked": status.queries,
                  "offline mode": String(status.offline_mode),
                }).map(([key, value]) => (
                  <tr key={key}>
                    <td className="mono tiny" style={{ width: 200, color: "var(--text-3)" }}>
                      {key}
                    </td>
                    <td className="mono tiny">{String(value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </div>
  );
}

function DataTableLite({ rows }) {
  return <BarList rows={rows} formatNote={(r) => r.note} />;
}

function Metric({ label, value, ratio, note }) {
  const good = ratio === undefined || ratio === null ? null : ratio >= 0.9;
  return (
    <div className="metric">
      <div className="metric__label">{label}</div>
      <div
        className={`metric__value${good === true ? " metric__value--good" : good === false ? " metric__value--warn" : ""}`}
      >
        {value}
      </div>
      {ratio !== undefined && ratio !== null ? (
        <div className="bar">
          <div
            className={`bar__fill${good ? "" : " bar__fill--warn"}`}
            style={{ width: `${Math.max(0, Math.min(1, ratio)) * 100}%` }}
          />
        </div>
      ) : null}
      {note ? <div className="metric__note">{note}</div> : null}
    </div>
  );
}

function CorpusPanel({ corpus }) {
  const rows = [
    ["Transcripts ingested", corpus.transcripts],
    ["Remembered", corpus.remembered],
    ["Deliberately ignored", `${corpus.ignored} (${formatPercent(corpus.ignore_share, 1)})`],
    ["Memories created", corpus.memories_created],
    ["Memories per transcript", corpus.memories_per_transcript],
    ["Superseded by a correction", corpus.memories_superseded],
    ["Skipped as duplicates", corpus.memories_duplicate],
    ["Rejected below threshold", corpus.memories_rejected],
    ["Memory store", JSON.stringify(corpus.memory_store)],
    ["Avg extraction latency", `${corpus.avg_extraction_latency_ms} ms`],
    ["Extraction cost", `$${corpus.extraction_cost_usd}`],
  ];
  return (
    <div className="card" style={{ padding: "18px 22px" }}>
      <div className="page__eyebrow" style={{ marginBottom: 12 }}>
        What the pipeline did to the corpus
      </div>
      <div className="table__scroll">
        <table className="table">
          <tbody>
            {rows.map(([label, value]) => (
              <tr key={label}>
                <td style={{ width: 260, color: "var(--text-3)" }}>{label}</td>
                <td className="mono">{String(value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Trace({ detail }) {
  return (
    <div className="trace">
      <div className="trace__step">
        <span className="trace__label">Question</span>
        {detail.question}
      </div>
      <div className="trace__step">
        <span className="trace__label">Retrieval</span>
        {detail.retrieved_memory_ids.length} memories retrieved in{" "}
        {Math.round(detail.retrieval_latency_ms)} ms · intent-scoped ranking
      </div>
      <div className="trace__step">
        <span className="trace__label">Memories used</span>
        {detail.used?.length ? (
          detail.used.map((m) => (
            <div key={m.memory_id} style={{ marginBottom: 10 }}>
              <div style={{ color: "var(--text-0)" }}>
                #{m.memory_id} [{m.type}] {m.content}
              </div>
              <div style={{ color: "var(--text-3)", paddingLeft: 14, marginTop: 3 }}>
                ↳ transcript #{m.source_transcript_id} · {formatStamp(m.source_timestamp)}
                {m.source_application ? ` · ${m.source_application}` : ""}
              </div>
              <div
                style={{
                  color: "var(--text-1)",
                  paddingLeft: 14,
                  fontStyle: "italic",
                  marginTop: 2,
                }}
              >
                “{m.source_text}”
              </div>
            </div>
          ))
        ) : (
          <span className="muted">none — Kivi abstained</span>
        )}
      </div>
      <div className="trace__step">
        <span className="trace__label">Decision</span>
        {detail.reasoning}
        <div style={{ marginTop: 6, color: "var(--text-3)" }}>
          model confidence {Number(detail.confidence).toFixed(2)} (self-reported) · support check{" "}
          {detail.supported ? "passed" : "FAILED"}
        </div>
      </div>
      <div className="trace__step">
        <span className="trace__label">Cost</span>
        retrieval {Math.round(detail.retrieval_latency_ms)} ms · model{" "}
        {Math.round(detail.llm_latency_ms)} ms · total {Math.round(detail.total_latency_ms)} ms ·{" "}
        {detail.input_tokens + detail.output_tokens} tokens · $
        {Number(detail.cost_usd).toFixed(5)} · {detail.model}
      </div>
    </div>
  );
}
