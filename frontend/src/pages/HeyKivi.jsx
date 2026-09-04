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
  const [error, setError] = useState(null);
  const endRef = useRef(null);
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
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, asking]);

  async function ask(text) {
    const trimmed = (text ?? question).trim();
    if (!trimmed || asking) return;
    setQuestion("");
    setError(null);
    setAsking(true);
    setTurns((prev) => [...prev, { role: "user", text: trimmed }]);
    try {
      const answer = await api.ask(trimmed);
      setTurns((prev) => [...prev, { role: "kivi", answer }]);
      api.queryAnalytics().then(setStats).catch(() => {});
    } catch (err) {
      setError(err);
      setTurns((prev) => prev.slice(0, -1));
      setQuestion(trimmed);
    } finally {
      setAsking(false);
      inputRef.current?.focus();
    }
  }

  async function dictate() {
    const text = question.trim();
    if (!text || asking) return;
    setQuestion("");
    setError(null);
    setAsking(true);
    setTurns((prev) => [...prev, { role: "user", text, dictation: true, application }]);
    try {
      const created = await api.addTranscript({
        // The corpus stores a raw recogniser pass alongside the formatted text.
        // Typed input has no recogniser, so we approximate one rather than
        // pretend the two are identical.
        raw_asr: text.toLowerCase().replace(/[.,!?;:]/g, ""),
        formatted_text: text,
        timestamp: new Date().toISOString(),
        application,
      });
      setTurns((prev) => [...prev, { role: "learned", transcript: created }]);
      onRefresh?.();
    } catch (err) {
      setError(err);
      setTurns((prev) => prev.slice(0, -1));
      setQuestion(text);
    } finally {
      setAsking(false);
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
      {/* On the opening screen the greeting is the header - the record screen
          leads with one line, not a title above a line. Once a conversation
          has started the page needs its own identity back, so the head
          returns. */}
      {started ? (
        <PageHead
          eyebrow="screen 2 · hey kivi"
          title="Ask, and see why"
          lede="Kivi answers only from what it has learned in your dictations. Every answer shows the memories behind it — and says so plainly when your history does not contain the answer."
        />
      ) : null}

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
            <Learned key={index} transcript={turn.transcript} />
          ) : (
            <Answer key={index} answer={turn.answer} restored={turn.restored} />
          ),
        )}
        {asking ? (
          <div className="turn turn--kivi">
            <KiviMark />
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className="thinking">
                <Spinner />
                <span>Working through your memory…</span>
              </div>
              <Pipeline pending />
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
/**
 * The opening screen, built on the shape of Kivi's own record page: a
 * marker-swiped line naming the moment, one card for the thing in hand, a
 * quiet list of what came before, and a single figure in the rail.
 */
function greeting(now = new Date()) {
  const h = now.getHours();
  if (h < 5) return ["night owl hours", "ask quietly."];
  if (h < 12) return ["morning", "what do you need to remember?"];
  if (h < 17) return ["afternoon", "ask what you already told me."];
  if (h < 22) return ["evening", "let's check what you said today."];
  return ["late", "ask, and I'll keep it short."];
}

function Welcome({ suggestions, onPick, stats, earlier, onEarlier, loadingEarlier, history }) {
  const [lead, tail] = greeting();
  const summary = stats?.summary;

  // The four suggestions are grouped by what each one demonstrates, so the
  // first thing a reader learns is that Kivi can also refuse - the behaviour
  // the product is really built around.
  const groups = [
    { label: "Recall", match: /prepare|discussing|say about|owe/i },
    { label: "Scheduling", match: /when is my|when is the/i },
    { label: "Drafting", match: /draft|message/i },
    { label: "Watch it refuse", match: /birthday/i },
  ]
    .map((g) => ({ ...g, picks: suggestions.filter((s) => g.match.test(s)) }))
    .filter((g) => g.picks.length);

  const first = groups[0]?.picks?.[0] || suggestions[0] || null;
  const rest = groups.flatMap((g) => g.picks.slice(0, 1)).filter((q) => q !== first);

  return (
    <div className="rec">
      <div style={{ minWidth: 0 }}>
        <h2 className="rec__title">
          <mark>{lead}</mark> — {tail}
        </h2>

        <div className="rec__card">
          <div className="rec__card-label">try asking</div>
          <div className="rec__card-body">
            {first || "Ask me about the people you work with, what you have coming up, or what you still owe someone."}
          </div>
          <div className="rec__card-foot">
            <span>
              <b>Hey Kivi</b> answers only from your dictations
            </span>
            <span>
              press <span className="keycap">Enter</span> to ask
            </span>
          </div>
        </div>

        {rest.length ? (
          <>
            <div className="rec__section">
              <span className="rec__section-title">or try one of these</span>
              {earlier ? (
                <button className="rec__section-link" onClick={onEarlier} disabled={loadingEarlier}>
                  {loadingEarlier ? "loading…" : `${earlier} earlier →`}
                </button>
              ) : null}
            </div>
            {groups.map((g) =>
              g.picks.slice(0, 1).map((q) => (
                <button className="rec__row" key={q} onClick={() => onPick(q)}>
                  <span className="rec__row-mark" aria-hidden="true" />
                  <span className="rec__row-text">{q}</span>
                  <span className="rec__row-app">{g.label}</span>
                </button>
              )),
            )}
          </>
        ) : null}
      </div>

      <aside className="rec__panel">
        <div className="rec__figure">{summary?.total ?? 0}</div>
        <div className="rec__figure-label">
          question{summary?.total === 1 ? "" : "s"} answered from memory
        </div>
        <div className="rec__figure-note">
          {summary?.total ? "every one traceable to what you said" : "nothing asked yet — start above"}
        </div>

        {summary?.total ? (
          <div className="rec__panel-rows">
            <div className="rec__panel-row">
              <span>refused honestly</span>
              <b>{summary.abstained}</b>
            </div>
            <div className="rec__panel-row">
              <span>grounded in memory</span>
              <b>{formatPercent(summary.supported_rate, 0)}</b>
            </div>
            <div className="rec__panel-row">
              <span>average answer</span>
              <b>{Math.round(summary.avg_total_latency_ms)} ms</b>
            </div>
          </div>
        ) : null}
      </aside>
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
 * The route a question takes, live while it runs and settled once it lands.
 *
 * The advance is driven by measured timings rather than invented ones. Across
 * the queries logged by this installation the median split is:
 *
 *     read the question + search memory     ~154 ms   (about 6% of the wait)
 *     ask the model                        ~2417 ms   (about 90%)
 *
 * So the first two steps really do go past almost immediately and the wait
 * really does sit in the last one. Stepping on those numbers reflects where
 * the time goes; it does not pretend to observe a stage the client cannot see.
 *
 * Two rules keep it honest. Only the elapsed clock is shown while running -
 * never a per-stage duration, which would be a guess presented as a
 * measurement. And when the answer arrives every row is replaced by what the
 * backend actually reported, including the total, so an estimate never
 * survives into the record.
 */
const STAGE_MS = [90, 154, 210];

function Pipeline({ answer = null, pending = false }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!pending) return undefined;
    const started = Date.now();
    setElapsed(0);
    const id = setInterval(() => setElapsed(Date.now() - started), 100);
    return () => clearInterval(id);
  }, [pending]);

  const d = answer?.diagnostics || {};
  const retrieved = answer?.retrieved_memory_ids?.length;
  const used = answer?.used_memory_ids?.length;
  const ms = (v) => (v || v === 0 ? `${Math.round(v)} ms` : null);

  // Which step is running: the first threshold the clock has not passed.
  const current = pending ? STAGE_MS.findIndex((t) => elapsed < t) : -1;
  const running = pending ? (current === -1 ? 3 : current) : -1;

  const stages = [
    {
      name: "Read the question",
      does: "Works out what is being asked and which people or projects it names.",
      fact: answer
        ? [answer.intent, answer.entities?.length ? answer.entities.join(", ") : null]
            .filter(Boolean)
            .join(" · ")
        : null,
    },
    {
      name: "Search memory",
      does: "Scores every active memory on meaning, wording and recency, and keeps the best few.",
      fact: answer
        ? [retrieved != null ? `${retrieved} retrieved` : null, ms(d.retrieval_latency_ms)]
            .filter(Boolean)
            .join(" · ")
        : null,
    },
    {
      name: "Check the memory supports it",
      does: "Refuses rather than guesses if nothing retrieved actually mentions the topic.",
      fact: answer
        ? answer.abstained
          ? "abstained — nothing supported it"
          : answer.supported
            ? "supported"
            : "unsupported"
        : null,
    },
    {
      name: "Answer from what was found",
      does: "Builds the answer only from the memories it kept, and cites them.",
      fact: answer
        ? [
            used != null ? `${used} used` : null,
            ms(d.llm_latency_ms),
            d.provider ? `${d.provider}${d.model && d.model !== d.provider ? ` · ${d.model}` : ""}` : null,
          ]
            .filter(Boolean)
            .join(" · ")
        : null,
    },
  ];

  return (
    <div className={`pipe${pending ? " pipe--pending" : ""}`}>
      <div className="pipe__head">
        {pending ? "working" : "how this answer was produced"}
        <span className="pipe__total mono">
          {pending
            ? `${(elapsed / 1000).toFixed(1)}s`
            : d.total_latency_ms
              ? `${Math.round(d.total_latency_ms)} ms total`
              : ""}
        </span>
      </div>

      <ol className="pipe__list">
        {stages.map((st, i) => {
          const state = pending ? (i < running ? "done" : i === running ? "run" : "wait") : "done";
          return (
            <li className={`pipe__step pipe__step--${state}`} key={st.name}>
              <span className="pipe__n">
                {state === "done" ? "✓" : state === "run" ? <span className="pipe__pulse" /> : i + 1}
              </span>
              <div className="pipe__body">
                <div className="pipe__name">{st.name}</div>
                <div className="pipe__does">{st.does}</div>
                {st.fact ? <div className="pipe__fact mono">{st.fact}</div> : null}
                {state === "run" ? <div className="pipe__fact mono">running…</div> : null}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function Answer({ answer, restored = false }) {
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
    <div className="turn turn--kivi">
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
        <Pipeline answer={answer} />

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
function Learned({ transcript }) {
  const memories = (transcript.memories || []).filter((m) => m.status !== "REJECTED");
  const rejected = (transcript.memories || []).filter((m) => m.status === "REJECTED");
  const extraction = transcript.extraction || {};
  const ignored = !memories.length;

  return (
    <div className="turn turn--kivi">
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
