/**
 * Screen 2 — Hey Kivi.
 *
 * The product itself: a question goes in, an answer comes back, and the
 * memories behind it are named.
 *
 * The screen is built around one idea — an answer is not just text, it is a
 * *claim with a status*. Three claims are possible and they mean very different
 * things, so each gets its own colour, icon and wording before you read a word
 * of the answer:
 *
 *   grounded     Kivi found support and is telling you what it knows
 *   not in       Kivi looked and has nothing — it is refusing, not failing
 *     history
 *   conflicting  Kivi holds two live answers and will not pick one for you
 *
 * Confidence sits on the answer rather than buried in diagnostics, right next
 * to the independent support check — deliberately side by side, because they
 * disagree sometimes and the disagreement is the informative part.
 *
 * Sources are collapsed by default. On a question that used four memories the
 * expanded list is a wall of text, and the count is usually all you want.
 */

import { useEffect, useRef, useState } from "react";
import { api, formatPercent, formatStamp } from "../services/api";
import { ErrorBanner, PageHead, Pill, Spinner, TypePill } from "../components/ui";

export default function HeyKivi({ onRefresh }) {
  const [turns, setTurns] = useState([]);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  // "ask" queries memory; "dictate" adds to it. Both live on this screen
  // because Hey Kivi is meant to be the one place you talk to Kivi — dictation
  // is a thing you do *inside* the conversation, not a separate destination.
  const [mode, setMode] = useState("ask");
  const [application, setApplication] = useState("Notes");
  // Past turns are on the server - every question is logged with its answer and
  // its retrieval - but they are not pulled in automatically. Reopening the
  // screen into somebody else's half-finished conversation is disorienting, and
  // a restored turn is not free to render. So it is a button.
  const [earlier, setEarlier] = useState(null); // null = not checked yet
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [suggestions, setSuggestions] = useState([]);
  const [stats, setStats] = useState(null);
  // The stages the server has reported for the question in flight. A stage
  // arrives when it finishes; the three that call a model announce themselves
  // first, so the row that is genuinely waiting says so while it waits.
  const [live, setLive] = useState([]);
  const [error, setError] = useState(null);
  const endRef = useRef(null);
  // Where to scroll when a reply lands. Following the bottom of the page is
  // right while the question is in flight - the trace is growing and you want
  // to watch it - but wrong the moment the answer arrives, because the answer
  // is above the trace and the bottom of the page is below all of it.
  const replyRef = useRef(null);
  const inputRef = useRef(null);
  // StrictMode runs mount effects twice in development. Without this the
  // deep-linked question would be asked - and billed - twice.
  const deepLinked = useRef(false);

  useEffect(() => {
    api.suggestions().then(setSuggestions).catch(() => setSuggestions([]));
    api.queryAnalytics().then(setStats).catch(() => setStats(null));
    // Only the count, so the button can say how much there is - or not appear.
    api.history(50).then((rows) => setEarlier(rows.length)).catch(() => setEarlier(0));

    // A question can be deep-linked: #/kivi?q=When%20is%20my%20meeting…
    // Useful for sharing a specific result, and for pointing a reviewer at one.
    const query = window.location.hash.split("?")[1];
    const q = query ? new URLSearchParams(query).get("q") : null;
    if (q && !deepLinked.current) {
      deepLinked.current = true;
      ask(q);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (asking) {
      endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
      return;
    }
    const last = turns[turns.length - 1];
    if (last && (last.role === "kivi" || last.role === "learned")) {
      replyRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    } else {
      endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [turns, asking]);

  // One event, folded into the list. An announced stage is a placeholder; when
  // the finished event for the same stage arrives it replaces the placeholder
  // rather than appearing beneath it.
  function onStage(event) {
    setLive((prev) => {
      const last = prev[prev.length - 1];
      if (last?.pending && last.stage === event.stage) return [...prev.slice(0, -1), event];
      return [...prev, event];
    });
  }

  async function ask(text) {
    const trimmed = (text ?? question).trim();
    if (!trimmed || asking) return;
    setQuestion("");
    setError(null);
    setAsking(true);
    setLive([]);
    setTurns((prev) => [...prev, { role: "user", text: trimmed }]);

    // Collected here as well as in state: the turn keeps its own copy of the
    // trace, so scrolling back to an earlier answer still shows how that
    // answer in particular was reached.
    const trace = [];
    const collect = (event) => {
      const last = trace[trace.length - 1];
      if (last?.pending && last.stage === event.stage) trace[trace.length - 1] = event;
      else trace.push(event);
      onStage(event);
    };

    try {
      let answer;
      try {
        answer = await api.askStreaming(trimmed, collect);
      } catch (streamFailure) {
        // A proxy that buffers event-streams, or an older backend, should cost
        // the trace and nothing else. The answer is what the user asked for.
        answer = await api.ask(trimmed);
        trace.length = 0;
      }
      setTurns((prev) => [
        ...prev,
        { role: "kivi", answer, stages: trace.filter((e) => !e.pending) },
      ]);
      api.queryAnalytics().then(setStats).catch(() => {});
    } catch (err) {
      setError(err);
      setTurns((prev) => prev.slice(0, -1));
      setQuestion(trimmed);
    } finally {
      setAsking(false);
      setLive([]);
      inputRef.current?.focus();
    }
  }

  async function dictate() {
    const text = question.trim();
    if (!text || asking) return;
    setQuestion("");
    setError(null);
    setAsking(true);
    setLive([]);
    setTurns((prev) => [...prev, { role: "user", text, dictation: true, application }]);

    const trace = [];
    const collect = (event) => {
      const last = trace[trace.length - 1];
      if (last?.pending && last.stage === event.stage) trace[trace.length - 1] = event;
      else trace.push(event);
      onStage(event);
    };

    try {
      const payload = {
        // The corpus stores a raw recogniser pass alongside the formatted text.
        // Typed input has no recogniser, so we approximate one rather than
        // pretend the two are identical.
        raw_asr: text.toLowerCase().replace(/[.,!?;:]/g, ""),
        formatted_text: text,
        timestamp: new Date().toISOString(),
        application,
      };

      let created;
      try {
        const result = await api.dictateStreaming(payload, collect);
        created = result.transcript;
      } catch (streamFailure) {
        created = await api.addTranscript(payload);
        trace.length = 0;
      }
      setTurns((prev) => [
        ...prev,
        { role: "learned", transcript: created, stages: trace.filter((e) => !e.pending) },
      ]);
      onRefresh?.();
    } catch (err) {
      setError(err);
      setTurns((prev) => prev.slice(0, -1));
      setQuestion(text);
    } finally {
      setAsking(false);
      setLive([]);
      inputRef.current?.focus();
    }
  }

  async function loadEarlier() {
    if (loadingEarlier) return;
    setLoadingEarlier(true);
    try {
      const rows = await api.history(50);
      // The log is newest-first; a conversation reads oldest-first. Each row
      // becomes the two turns it originally was.
      const restored = rows
        .slice()
        .reverse()
        .flatMap((row) => [
          { role: "user", text: row.question },
          { role: "kivi", answer: row, restored: true },
        ]);
      setTurns((prev) => [...restored, ...prev]);
      setEarlier(0);
    } catch (err) {
      setError(err);
    } finally {
      setLoadingEarlier(false);
    }
  }

  const started = turns.length > 0 || asking;
  const dictateMode = mode === "dictate";

  return (
    <div className="page page--chat">
      <PageHead
        eyebrow="screen 2 · hey kivi"
        title="Ask, and see why"
        lede="Kivi answers only from what it has learned in your dictations. Every answer shows the memories behind it — and says so plainly when your history does not contain the answer."
      />

      <ErrorBanner error={error} />

      {!started ? (
        <Welcome
          suggestions={suggestions}
          onPick={ask}
          stats={stats}
          earlier={earlier}
          onEarlier={loadEarlier}
          loadingEarlier={loadingEarlier}
        />
      ) : null}

      {started && earlier ? (
        <div className="chat__earlier">
          <button className="chip" onClick={loadEarlier} disabled={loadingEarlier}>
            {loadingEarlier ? <Spinner /> : null} show {earlier} earlier question
            {earlier === 1 ? "" : "s"}
          </button>
        </div>
      ) : null}

      <div className="chat">
        {turns.map((turn, index) =>
          turn.role === "user" ? (
            <div className="turn turn--user" key={index}>
              <div className="turn__bubble">
                {turn.dictation ? (
                  <span className="turn__tag mono">dictated · {turn.application}</span>
                ) : null}
                {turn.text}
              </div>
            </div>
          ) : turn.role === "learned" ? (
            <Learned
              key={index}
              transcript={turn.transcript}
              stages={turn.stages}
              innerRef={index === turns.length - 1 ? replyRef : null}
            />
          ) : (
            <Answer
              key={index}
              answer={turn.answer}
              stages={turn.stages}
              restored={turn.restored}
              innerRef={index === turns.length - 1 ? replyRef : null}
            />
          ),
        )}
        {asking ? (
          <div className="turn turn--kivi">
            <KiviMark />
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className="thinking">
                <Spinner />
                <span>
                  {dictateMode ? "Working out what to keep…" : "Working through your memory…"}
                </span>
              </div>
              <Pipeline pending stages={live} />
            </div>
          </div>
        ) : null}
        <div ref={endRef} />
      </div>

      <div className="ask">
        <form
          className="ask__row"
          onSubmit={(e) => {
            e.preventDefault();
            if (dictateMode) dictate();
            else ask();
          }}
        >
          <div className="mode" role="group" aria-label="What to do with what you type">
            <button
              type="button"
              className="mode__btn"
              aria-pressed={!dictateMode}
              onClick={() => setMode("ask")}
            >
              Ask
            </button>
            <button
              type="button"
              className="mode__btn"
              aria-pressed={dictateMode}
              onClick={() => setMode("dictate")}
            >
              Dictate
            </button>
          </div>

          <input
            ref={inputRef}
            className="field ask__field"
            placeholder={
              dictateMode
                ? "Say something about your work — Kivi decides what to keep…"
                : "Ask about your work…"
            }
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            autoFocus
          />

          {dictateMode ? (
            <select
              className="field ask__app"
              value={application}
              onChange={(e) => setApplication(e.target.value)}
              aria-label="Which application you are dictating into"
            >
              {["Notes", "Slack", "Mail", "Linear", "Docs"].map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          ) : null}

          <button className="btn ask__send" type="submit" disabled={asking || !question.trim()}>
            {asking ? <Spinner /> : dictateMode ? "Dictate" : "Ask"}
          </button>
        </form>

        {started && !dictateMode && suggestions.length ? (
          <div className="suggestions">
            {suggestions.slice(0, 4).map((s) => (
              <button key={s} className="suggestion" onClick={() => ask(s)} disabled={asking}>
                {s}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- identity */
function KiviMark() {
  return (
    <span className="kivi-mark" aria-hidden="true">
      <span className="kivi-mark__dot" />
    </span>
  );
}

/* ---------------------------------------------------------------- welcome */
function Welcome({ suggestions, onPick, stats, earlier, onEarlier, loadingEarlier }) {
  // Grouped by what each question *demonstrates*, so the first thing a new
  // reader learns is that Kivi can also refuse — the behaviour the product is
  // really built around.
  const groups = [
    { label: "Recall", match: /prepare|discussing|say about|owe/i },
    { label: "Scheduling", match: /when is my|when is the/i },
    { label: "Drafting", match: /draft|message/i },
    { label: "Watch it refuse", match: /birthday/i },
  ]
    .map((g) => ({ ...g, picks: suggestions.filter((s) => g.match.test(s)) }))
    .filter((g) => g.picks.length);

  return (
    <div className="welcome">
      <div className="welcome__intro">
        <KiviMark />
        <p>
          Ask me about the people you work with, what you have coming up, or what you still
          owe someone. I'll only tell you what you've actually said — and I'll say so when
          I don't know.
        </p>
      </div>

      {groups.length ? (
        <div className="welcome__groups">
          {groups.map((g) => (
            <div className="welcome__group" key={g.label}>
              <div className="welcome__group-label">{g.label}</div>
              {g.picks.slice(0, 2).map((s) => (
                <button key={s} className="welcome__pick" onClick={() => onPick(s)}>
                  {s}
                </button>
              ))}
            </div>
          ))}
        </div>
      ) : null}

      {earlier ? (
        <div className="welcome__earlier">
          <button className="chip" onClick={onEarlier} disabled={loadingEarlier}>
            {loadingEarlier ? <Spinner /> : null} show {earlier} earlier question
            {earlier === 1 ? "" : "s"}
          </button>
        </div>
      ) : null}

      {stats?.summary?.total ? (
        <div className="welcome__stats">
          <span>
            <b>{stats.summary.total}</b> asked
          </span>
          <span>
            <b>{stats.summary.abstained}</b> refused honestly
          </span>
          <span>
            <b>{formatPercent(stats.summary.supported_rate, 0)}</b> grounded
          </span>
          <span>
            <b>{Math.round(stats.summary.avg_total_latency_ms)} ms</b> average
          </span>
        </div>
      ) : null}
    </div>
  );
}

/* ----------------------------------------------------------------- answer */
const SHAPES = {
  abstained: { tone: "warn", icon: "◌", label: "Not in your history" },
  conflict: { tone: "rose", icon: "!", label: "Conflicting memories" },
  unsupported: { tone: "rose", icon: "!", label: "Unsupported" },
  grounded: { tone: "good", icon: "✓", label: "Grounded" },
};

/**
 * The route a question takes, reported by the pipeline itself.
 *
 * The server narrates: each stage emits an event as it finishes, carrying the
 * values it actually computed. What follows is only about how to show nine of
 * those without burying the answer they produced.
 *
 * It reads as a collapsed line you can open - a sentence per step, in English,
 * with the numbers folded into the sentence. Earlier versions of this showed
 * the same data as a table of labels and values, and it was accurate and
 * unreadable: nobody scans nine tables to find out that a question took two
 * seconds because a model was thinking. The measured values are all still here;
 * they are just written as prose, with the raw facts one click further in for
 * anyone who wants to check them.
 */

/* The planner's intent names are identifiers, not English. */
const ASKING = {
  when: "when something is",
  who: "who someone is",
  prepare: "what to prepare",
  discussed: "what was discussed",
  preference: "a preference of yours",
  draft: "something to be drafted",
  why: "why something is the case",
  general: "your history in general",
};

/** One sentence describing what a stage did, built from what it reported. */
function narrate(stage) {
  const f = (key) => {
    const found = stage.facts?.find(([k]) => k === key);
    return found ? found[1] : undefined;
  };
  const list = (v) => (v === undefined || v === "" || v === "-" ? null : String(v));

  switch (stage.stage) {
    case "plan": {
      const names = list(f("names"));
      const asking = list(f("asking for"));
      const must = list(f("must be mentioned"));
      const bits = [];
      if (names && names !== "nobody Kivi knows") bits.push(`Recognised ${names}`);
      else bits.push("No name it already knows");
      if (asking) bits.push(`asking ${ASKING[asking] || `about ${asking}`}`);
      let out = bits.join(", ") + ".";
      if (must) out += ` An answer has to mention ${must}.`;
      if (f("time-sensitive")?.toString().startsWith("yes")) out += " Recency counts extra.";
      return out;
    }
    case "corpus":
      return `Loaded all ${f("candidates")} memories — ${f("current")} current and ${f(
        "superseded",
      )} that were later corrected, which are demoted rather than hidden.`;
    case "lexical":
      return `Built a word index over all ${f("documents indexed")} of them, ${f(
        "distinct words",
      )} distinct words. It is what rescues a rare name.`;
    case "embed":
      return `Hashed the question into a vector with ${f(
        "buckets used",
      )} buckets filled. No model, no download, same result on any machine.`;
    case "score":
      return `Scored all ${f(
        "scored",
      )} on six signals — meaning, wording and recency by weight, plus three structural bonuses added outright.`;
    case "rank": {
      const kept = f("kept");
      const dropped = f("dropped");
      const top = list(f("top score"));
      return `Kept ${kept}, dropped ${dropped}${top ? `. Best score ${top}` : ""}.`;
    }
    case "compose": {
      const by = list(f("answered by"));
      const cited = f("memories cited");
      const offered = f("memories offered");
      const outcome = list(f("outcome"));
      if (outcome === "declined to answer")
        return `${by} was given ${offered} memories and declined to answer from them.`;
      return `${by} wrote the answer from ${cited} of the ${offered} memories it was given, and cited them.`;
    }
    case "rescue":
      return `Nothing supported an answer, so the raw dictations were searched too — ${f(
        "outcome",
      )}.`;
    case "verify":
      return `${list(f("reason")) || f("verdict")}.`;
    case "provenance":
      return `Traced ${f("memories cited")} memories back to ${f(
        "source dictations",
      )} dictations, so the answer arrives attached to your own words.`;

    // the dictation path
    case "dictation":
      return `Stored ${f("length")} from ${f("application")}, exactly as said.`;
    case "extract":
      return `${f("verdict")} — found ${f("candidates found")} worth keeping. Decided by ${list(
        f("decided by"),
      )}.`;
    case "reject":
      return `Confidence ${f("confidence")} is below the ${f(
        "threshold",
      )} threshold, so it was stored as REJECTED rather than dropped.`;
    case "reconcile": {
      const n = f("existing memories in that slot");
      const verdict = f("verdict");
      const replaces = list(f("replaces"));
      return `Compared against ${n} memory/memories about the same thing: ${verdict}${
        replaces ? `, replacing ${replaces}` : ""
      }.`;
    }
    case "stored": {
      const parts = [];
      if (f("learned")) parts.push(`learned ${f("learned")}`);
      if (f("corrected an earlier memory"))
        parts.push(`corrected ${f("corrected an earlier memory")}`);
      if (f("already knew")) parts.push(`already knew ${f("already knew")}`);
      if (f("not confident enough")) parts.push(`${f("not confident enough")} not trusted`);
      return `Written down with its audit trail — ${parts.join(", ") || "nothing changed"}.`;
    }
    default:
      return stage.does || "";
  }
}

function Pipeline({ answer = null, stages = [], pending = false }) {
  const [elapsed, setElapsed] = useState(0);
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState(null);

  useEffect(() => {
    if (!pending) return undefined;
    const started = Date.now();
    setElapsed(0);
    const id = setInterval(() => setElapsed(Date.now() - started), 100);
    return () => clearInterval(id);
  }, [pending]);

  const rows = stages.length ? stages : fromDiagnostics(answer);
  if (!rows.length && !pending) return null;

  const done = rows.filter((r) => !r.pending);
  const total = rows.length ? rows[rows.length - 1].at_ms : 0;
  const active = rows.find((r) => r.pending);
  const shown = pending || open;

  // While it runs the summary line says what is happening right now; once it
  // lands it says what it cost, which is the only part still worth a glance.
  const summary = pending
    ? active
      ? active.label
      : done.length
        ? done[done.length - 1].label
        : "Working through your memory"
    : `Worked through your memory in ${fmtMs(total)}`;

  return (
    <div className={`think${shown ? " think--open" : ""}`}>
      <button
        className="think__line"
        onClick={() => !pending && setOpen((v) => !v)}
        aria-expanded={shown}
        disabled={pending}
      >
        <span className="think__caret" aria-hidden="true" />
        <span className={`think__summary${pending ? " think__summary--live" : ""}`}>
          {summary}
        </span>
        <span className="think__time mono">
          {pending ? `${(elapsed / 1000).toFixed(1)}s` : `${done.length} steps`}
        </span>
      </button>

      {shown ? (
        <ol className="think__steps">
          {rows.map((stage, i) => {
            const id = `${stage.stage}-${i}`;
            const isOpen = detail === id;
            const more = stage.facts?.length || stage.table || stage.note;
            return (
              <li className={`tk${stage.pending ? " tk--run" : ""}`} key={id}>
                <div className="tk__line">
                  <span className="tk__name">{stage.label}</span>
                  <span className="tk__ms mono">
                    {stage.pending ? "…" : fmtMs(stage.ms)}
                  </span>
                </div>
                <p className="tk__says">
                  {stage.pending ? stage.does : narrate(stage)}
                </p>

                {!stage.pending && more ? (
                  <button
                    className="tk__more"
                    onClick={() => setDetail(isOpen ? null : id)}
                    aria-expanded={isOpen}
                  >
                    {isOpen ? "hide the numbers" : "show the numbers"}
                  </button>
                ) : null}

                {isOpen ? (
                  <div className="tk__detail">
                    {stage.facts?.length ? (
                      <dl className="tk__facts">
                        {stage.facts.map(([label, value]) => (
                          <div className="tk__fact" key={label}>
                            <dt>{label}</dt>
                            <dd className="mono">{String(value)}</dd>
                          </div>
                        ))}
                      </dl>
                    ) : null}
                    {stage.table ? <StageTable table={stage.table} /> : null}
                    {stage.note ? <p className="tk__note">{stage.note}</p> : null}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ol>
      ) : null}
    </div>
  );
}

function StageTable({ table }) {
  return (
    <div className="st__table-scroll">
      <table className="st__table">
        <thead>
          <tr>
            {table.head.map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j} className={j === 0 ? "" : "mono st__num"}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// A stage that really took 0.27 ms should not be reported as "0 ms" - that
// reads as "not measured" rather than "too fast to matter".
const fmtMs = (v) =>
  v === null || v === undefined
    ? "—"
    : v >= 1000
      ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)} s`
      : v < 0.5
        ? "<1 ms"
        : `${Math.round(v)} ms`;

/**
 * Two stages reconstructed from a logged answer, for turns restored from the
 * query log. Deliberately only two: those are the only ones the log timed, and
 * inventing the other seven from an average would be exactly the estimate this
 * component was rewritten to stop making.
 */
function fromDiagnostics(answer) {
  if (!answer) return [];
  const d = answer.diagnostics || {};
  if (!d.total_latency_ms) return [];
  const retrieval = d.retrieval_latency_ms || 0;
  return [
    {
      stage: "rank",
      label: "Search memory",
      does: "Scored every memory on meaning, wording, recency and three structural signals, and kept the best few.",
      ms: retrieval,
      at_ms: retrieval,
      facts: [
        ["scored", d.memories_considered],
        ["kept", d.memories_retrieved],
        ["asking for", answer.intent],
        ["names", answer.entities?.join(", ") || "nobody Kivi knows"],
      ].filter(([, v]) => v !== undefined && v !== null),
    },
    {
      stage: "compose",
      label: "Write an answer, using only those",
      does: "Built the answer from the memories it kept, and cited them.",
      ms: d.llm_latency_ms || 0,
      at_ms: d.total_latency_ms,
      facts: [
        ["answered by", d.provider ? `${d.provider} - ${d.model}` : null],
        ["memories cited", d.memories_used],
        ["tokens in / out", d.input_tokens ? `${d.input_tokens} / ${d.output_tokens}` : null],
        [
          "outcome",
          answer.abstained ? "declined to answer" : answer.supported ? "supported" : "unsupported",
        ],
      ].filter(([, v]) => v !== undefined && v !== null),
      note: "Restored from the query log, which timed retrieval and the model but not the stages inside them.",
    },
  ];
}

function Answer({ answer, stages = [], restored = false, innerRef = null }) {
  const [showSources, setShowSources] = useState(false);
  const [showWorking, setShowWorking] = useState(false);
  const d = answer.diagnostics || {};

  const key = answer.abstained
    ? "abstained"
    : answer.conflict
      ? "conflict"
      : answer.supported
        ? "grounded"
        : "unsupported";
  const shape = SHAPES[key];
  const used = answer.used_memory_ids.length;
  const confidence = answer.confidence ?? 0;

  return (
    <div className="turn turn--kivi" ref={innerRef}>
      <KiviMark />

      <div className="answer">
        {/* the status of the claim, before the words of it */}
        <div className={`verdict verdict--${shape.tone}`}>
          <span className="verdict__icon">{shape.icon}</span>
          <span className="verdict__label">
            {shape.label}
            {key === "grounded" ? ` in ${used} memor${used === 1 ? "y" : "ies"}` : ""}
          </span>

          <span
            className="verdict__meter"
            title="The answering model's own estimate. Self-reported and not calibrated — the badge on the left is the independent check."
          >
            <span className="verdict__meter-track">
              <span
                className="verdict__meter-fill"
                style={{ width: `${Math.round(confidence * 100)}%` }}
              />
            </span>
            <span className="verdict__meter-value mono">{confidence.toFixed(2)}</span>
          </span>
        </div>

        <p className="answer__body">{answer.answer}</p>

        <div className="answer__actions">
          {answer.sources?.length ? (
            <button className="chip" onClick={() => setShowSources((v) => !v)}>
              {showSources ? "Hide" : "Show"} {answer.sources.length} source
              {answer.sources.length === 1 ? "" : "s"}
            </button>
          ) : null}
          <button className="chip" onClick={() => setShowWorking((v) => !v)}>
            {showWorking ? "Hide working" : "Show working"}
          </button>
          <span className="answer__meta mono">
            {restored ? "earlier · " : ""}
            {Number.isFinite(d.total_latency_ms) ? `${Math.round(d.total_latency_ms)} ms` : ""}
            {d.total_tokens ? ` · ${d.total_tokens} tok` : ""}
            {d.estimated_cost_usd ? ` · $${d.estimated_cost_usd.toFixed(4)}` : ""}
            {d.model ? ` · ${d.model}` : ""}
          </span>
        </div>

        {showSources && answer.sources?.length ? (
          <div className="sources">
            {answer.sources.map((source) => (
              <div className="source" key={source.memory_id}>
                {/* A negative id is a rescued dictation, not a memory — Kivi
                    never learned it, so label it rather than showing "#-31". */}
                <div className="source__id mono">
                  {source.memory_id < 0 ? `dictation #${-source.memory_id}` : `#${source.memory_id}`}
                </div>
                <div>
                  <div className="source__content">{source.memory_content}</div>
                  {source.excerpt ? <div className="source__excerpt">“{source.excerpt}”</div> : null}
                  <div className="source__meta mono">
                    {formatStamp(source.timestamp)}
                    {source.application ? ` · ${source.application}` : ""}
                    {source.transcript_id ? ` · transcript #${source.transcript_id}` : ""}
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : null}

        {/* The route the answer took, always visible: the same four rows that
            were empty while it was pending, now carrying what actually
            happened. "Show working" below goes a level deeper into the
            retrieval ranking. */}
        <Pipeline answer={answer} stages={stages} />

        {showWorking ? <Working answer={answer} /> : null}
      </div>
    </div>
  );
}

function Working({ answer }) {
  return (
    <div className="working">
      <div className="working__block">
        <div className="working__label">Why this answer</div>
        <p className="small" style={{ color: "var(--text-1)" }}>
          {answer.reasoning}
        </p>
      </div>

      <div className="working__block">
        <div className="working__label">
          Retrieval — {answer.retrieval_detail?.length || 0} candidates ranked
        </div>
        <div className="table__scroll">
          <table className="table">
            <thead>
              <tr>
                <th>Memory</th>
                <th>Type</th>
                <th>Score</th>
                <th>Signals</th>
                <th>Used</th>
              </tr>
            </thead>
            <tbody>
              {answer.retrieval_detail?.map((row) => (
                <tr key={row.memory_id}>
                  <td style={{ maxWidth: 340 }}>
                    <span className="mono tiny muted">#{row.memory_id}</span> {row.content}
                  </td>
                  <td>
                    <TypePill type={row.type} />
                  </td>
                  <td className="mono">{row.score.toFixed(3)}</td>
                  <td>
                    <ScoreBar row={row} />
                    <div className="mono tiny muted" style={{ marginTop: 4 }}>
                      sem {row.semantic.toFixed(2)} · lex {row.lexical.toFixed(2)} · rec{" "}
                      {row.recency.toFixed(2)}
                    </div>
                  </td>
                  <td>
                    {answer.used_memory_ids.includes(row.memory_id) ? (
                      <Pill tone="good">yes</Pill>
                    ) : (
                      <span className="muted tiny">no</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function ScoreBar({ row }) {
  const total = Math.max(0.0001, row.semantic + row.lexical + row.recency);
  const pct = (v) => `${((v / total) * 100).toFixed(1)}%`;
  return (
    <div className="score-bar" title="semantic / lexical / recency">
      <span className="score-bar__sem" style={{ width: pct(row.semantic) }} />
      <span className="score-bar__lex" style={{ width: pct(row.lexical) }} />
      <span className="score-bar__rec" style={{ width: pct(row.recency) }} />
    </div>
  );
}

/* ---------------------------------------------------------------- learned */
/**
 * Kivi's reply to a dictation: what it took from what you just said.
 *
 * This is the moment the whole product turns on — you say something, and Kivi
 * tells you, immediately and in your own words, what it will now remember. It
 * also tells you when it decided to remember *nothing*, with the reason, which
 * is the more important half: a memory system you cannot see deciding is one
 * you cannot trust. Every memory shown here is answerable the moment it lands.
 */
function Learned({ transcript, stages = [], innerRef = null }) {
  const memories = (transcript.memories || []).filter((m) => m.status !== "REJECTED");
  const rejected = (transcript.memories || []).filter((m) => m.status === "REJECTED");
  const extraction = transcript.extraction || {};
  const ignored = !memories.length;

  return (
    <div className="turn turn--kivi" ref={innerRef}>
      <KiviMark />

      <div className="answer">
        <div className={`verdict verdict--${ignored ? "muted" : "good"}`}>
          <span className="verdict__icon">{ignored ? "◌" : "✓"}</span>
          <span className="verdict__label">
            {ignored
              ? "Nothing worth keeping"
              : `Remembered ${memories.length} thing${memories.length === 1 ? "" : "s"}`}
          </span>
          <span className="verdict__meter-value mono">#{transcript.id}</span>
        </div>

        {extraction.rationale ? <p className="answer__body">{extraction.rationale}</p> : null}

        {memories.length ? (
          <div className="learned">
            {memories.map((memory) => (
              <div className="learned__row" key={memory.id}>
                <TypePill type={memory.type} />
                <div className="learned__text">{memory.content}</div>
              </div>
            ))}
          </div>
        ) : null}

        {rejected.length ? (
          <div className="learned__note small">
            {rejected.length} below the confidence threshold, kept as not-trusted rather than
            stored as fact.
          </div>
        ) : null}

        <Pipeline stages={stages} />

        <div className="answer__actions">
          <span className="answer__meta mono">
            {extraction.provider ? `${extraction.provider} · ` : ""}
            {extraction.latency_ms ? `${Math.round(extraction.latency_ms)} ms · ` : ""}
            {extraction.input_tokens
              ? `${extraction.input_tokens}in/${extraction.output_tokens}out · `
              : ""}
            saved to transcript #{transcript.id}
          </span>
        </div>
      </div>
    </div>
  );
}
