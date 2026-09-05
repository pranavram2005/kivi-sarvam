/**
 * How the system works, in seven sections and ten diagrams.
 *
 * This screen exists because the assignment asks that "an engineer can inspect
 * why memory did or did not affect a result", and a reader who has just opened
 * the app has no way to know what happens between speaking and being answered.
 * The Inspector shows evidence for one particular answer; this shows the shape
 * of the machine that produced it.
 *
 * The diagrams are hand-drawn SVG rather than a rendered diagram language. They
 * are fixed pictures, not generated ones, so a library would be weight
 * without benefit - and inline paths inherit `currentColor`, which is what lets
 * them work in both themes without a second asset.
 */
import { useEffect, useState } from "react";
import { api } from "../services/api";
import { PageHead, Pill } from "../components/ui";

/* --------------------------------------------------------------- primitives */

/** A labelled box. `tone` picks it out as a decision or a store. */
function Box({ x, y, w = 116, h = 40, title, sub, tone = "plain" }) {
  return (
    <g className={`dg__box dg__box--${tone}`}>
      <rect x={x} y={y} width={w} height={h} rx="6" />
      <text x={x + w / 2} y={sub ? y + h / 2 - 3 : y + h / 2 + 4} className="dg__t">
        {title}
      </text>
      {sub ? (
        <text x={x + w / 2} y={y + h / 2 + 12} className="dg__s">
          {sub}
        </text>
      ) : null}
    </g>
  );
}

/** An arrow between two points, optionally labelled above the line. */
function Arrow({ x1, y1, x2, y2, label }) {
  const midX = (x1 + x2) / 2;
  return (
    <g className="dg__arrow">
      <path d={`M${x1} ${y1} L${x2} ${y2}`} markerEnd="url(#dg-head)" />
      {label ? (
        <text x={midX} y={y1 - 7} className="dg__l">
          {label}
        </text>
      ) : null}
    </g>
  );
}

/** The shared arrowhead. One definition, referenced by every diagram. */
function Defs() {
  return (
    <defs>
      <marker id="dg-head" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
        <path d="M0 0.6 L7.4 4 L0 7.4 z" className="dg__head" />
      </marker>
    </defs>
  );
}

function Figure({ caption, viewBox, height, children }) {
  return (
    <figure className="dg">
      <div className="dg__scroll">
        <svg viewBox={viewBox} style={{ height, width: "100%" }} role="img" aria-label={caption}>
          <Defs />
          {children}
        </svg>
      </div>
      <figcaption className="dg__cap">{caption}</figcaption>
    </figure>
  );
}

/**
 * What each retrieval signal actually contributed, read from the query log.
 *
 * Six signals go into a score and only three of them carry a visible weight,
 * which makes retrieval easy to describe wrongly - "0.55 semantic, 0.30
 * lexical, 0.15 recency" is under half of it. Rather than restate that claim in
 * prose, this asks the system: every answered question stored its full ranking
 * per signal, and this is the average of what each one put into the memory the
 * score actually chose.
 *
 * It moves as questions are asked, which is the point - it is a measurement of
 * this installation, not a constant baked into the page.
 */
function SignalTable() {
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .queryAnalytics()
      .then((d) => alive && setData(d?.signal_contributions || null))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, []);

  if (failed) return null;
  if (!data) return <div className="sig sig--empty">measuring…</div>;
  if (!data.queries) {
    return (
      <div className="sig sig--empty">
        No questions asked yet — ask something on Hey Kivi and this fills in.
      </div>
    );
  }

  const top = data.signals[0]?.share || 1;

  return (
    <div className="sig">
      <div className="sig__head">
        <span>mean contribution to the memory the score chose</span>
        <span className="mono">{data.queries} questions</span>
      </div>

      {data.signals.map((s) => (
        <div className="sig__row" key={s.key}>
          <span className="sig__label">
            {s.label}
            {s.weighted ? null : <span className="sig__tag">structural</span>}
          </span>
          <span className="sig__bar">
            <span
              className={`sig__fill${s.weighted ? "" : " sig__fill--structural"}`}
              style={{ width: `${(s.share / top) * 100}%` }}
            />
          </span>
          <span className="sig__pct mono">{(s.share * 100).toFixed(1)}%</span>
        </div>
      ))}

      <div className="sig__foot">
        The three signals with no configurable weight account for{" "}
        <b>{(data.structural_share * 100).toFixed(0)}%</b> of the score.
      </div>
    </div>
  );
}

/**
 * A short index, so a reader can go straight to the part they came for.
 *
 * Buttons rather than anchors on purpose: this app routes on the URL hash
 * (`#/kivi`, `#/how`), so an `href="#algorithms"` would not scroll anywhere -
 * it would fire a hashchange and navigate to a screen that does not exist.
 */
const SECTIONS = [
  ["ingest", "1", "A dictation becomes memory"],
  ["reconcile", "2", "Deciding whether Kivi already knew it"],
  ["query", "3", "A question becomes an answer"],
  ["storage", "4", "What is actually stored"],
  ["algorithms", "5", "The algorithms, and why these ones"],
  ["vectordb", "6", "Would a vector database help?"],
  ["advanced", "7", "Techniques not used, and why"],
];

function Index() {
  const go = (id) => {
    const target = document.getElementById(id);
    if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <nav className="ix" aria-label="Sections on this page">
      <div className="ix__head mono">on this page</div>
      <ol className="ix__list">
        {SECTIONS.map(([id, n, title]) => (
          <li key={id}>
            <button className="ix__link" onClick={() => go(id)}>
              <span className="ix__n mono">{n}</span>
              <span className="ix__title">{title}</span>
            </button>
          </li>
        ))}
      </ol>
    </nav>
  );
}

/* ----------------------------------------------------------------- the page */

export default function HowItWorks({ status }) {
  const memories = status?.memories || {};
  const active = memories.ACTIVE ?? 0;
  const superseded = memories.SUPERSEDED ?? 0;
  const rejected = memories.REJECTED ?? 0;

  return (
    <div className="page">
      <PageHead
        eyebrow="screen 5 · how it works"
        title="What happens between speaking and being answered"
        lede="The same system from several angles: what happens to a dictation, how Kivi decides whether it already knew something, what a question goes through, what is actually stored, the algorithms underneath it, and the ones that were considered and left out."
      />

      <Index />

      {/* ------------------------------------------------ 1. ingest */}
      <section className="how" id="ingest">
        <h2 className="how__h">1 · A dictation becomes memory</h2>
        <p className="how__p">
          Every dictation is stored first, exactly as it was said, and nothing that happens
          afterwards can change it. Extraction then decides whether anything in it is worth
          keeping — and most of the time for filler, it decides no.
        </p>

        <Figure
          caption="Storing comes before understanding, so a wrong judgement never costs you the words."
          viewBox="0 0 760 150"
          height={150}
        >
          <Box x={4} y={54} w={104} title="Dictation" sub="raw + formatted" />
          <Arrow x1={112} y1={74} x2={148} y2={74} />
          <Box x={152} y={54} w={104} title="Stored" sub="never altered" tone="store" />
          <Arrow x1={260} y1={74} x2={296} y2={74} label="extract" />
          <Box x={300} y={54} w={118} title="Worth keeping?" tone="decide" />

          {/* ignored — the branch that matters most */}
          <path d="M359 54 L359 22 L470 22" className="dg__arrow-path" markerEnd="url(#dg-head)" />
          <text x={392} y={15} className="dg__l">no</text>
          <Box x={474} y={4} w={126} h={36} title="Ignored" sub="with a reason" tone="muted" />

          {/* kept */}
          <Arrow x1={422} y1={74} x2={458} y2={74} label="yes" />
          <Box x={462} y={54} w={126} title="Typed + scored" sub="fact · preference · …" />

          {/* below threshold */}
          <path d="M525 94 L525 124 L640 124" className="dg__arrow-path" markerEnd="url(#dg-head)" />
          <text x={558} y={118} className="dg__l">low confidence</text>
          <Box x={644} y={106} w={112} h={36} title="Rejected" sub="kept, not used" tone="muted" />

          <Arrow x1={592} y1={74} x2={628} y2={74} />
          <Box x={632} y={54} w={124} title="Reconcile" sub="against what's known" tone="decide" />
        </Figure>
      </section>

      {/* ------------------------------------------------ 2. reconciliation */}
      <section className="how" id="reconcile">
        <h2 className="how__h">2 · Deciding whether Kivi already knew it</h2>
        <p className="how__p">
          A new memory is never written blindly. Kivi first looks for what it already believes
          about the same subject and attribute — <em>the Atlas review's time</em>, <em>who leads
          Project Forge</em> — and then decides which of four things is happening.
        </p>

        <Figure
          caption="Nothing is deleted. A corrected memory keeps its row and gains a pointer to what replaced it."
          viewBox="0 0 760 194"
          height={194}
        >
          <Box x={4} y={78} w={124} title="New memory" sub="subject + attribute" />
          <Arrow x1={132} y1={98} x2={168} y2={98} />
          <Box x={172} y={78} w={128} title="What do we" sub="already believe?" tone="decide" />

          <path d="M304 98 L340 98" className="dg__arrow-path" markerEnd="url(#dg-head)" />

          <Box x={344} y={4} w={122} h={36} title="NEW" sub="nothing matched" />
          <Box x={344} y={52} w={122} h={36} title="DUPLICATE" sub="already known" tone="muted" />
          <Box x={344} y={100} w={122} h={36} title="SUPERSEDES" sub="this replaces it" tone="good" />
          <Box x={344} y={148} w={122} h={36} title="CONFLICTS" sub="both kept, flagged" tone="warn" />

          {/* fan from the decision */}
          <path d="M340 98 L344 22" className="dg__thin" />
          <path d="M340 98 L344 70" className="dg__thin" />
          <path d="M340 98 L344 118" className="dg__thin" />
          <path d="M340 98 L344 166" className="dg__thin" />

          <Arrow x1={470} y1={118} x2={506} y2={118} />
          <Box x={510} y={100} w={134} title="Old row kept" sub="status SUPERSEDED" tone="store" />
          <Arrow x1={470} y1={166} x2={506} y2={166} />
          <Box x={510} y={148} w={134} h={36} title="Both surfaced" sub="never picked silently" tone="store" />

          <text x={664} y={122} className="dg__s dg__s--left">
            penalised at
          </text>
          <text x={664} y={134} className="dg__s dg__s--left">
            retrieval, not
          </text>
          <text x={664} y={146} className="dg__s dg__s--left">
            removed
          </text>
        </Figure>
      </section>

      {/* ------------------------------------------------ 3. query */}
      <section className="how" id="query">
        <h2 className="how__h">3 · A question becomes an answer</h2>
        <p className="how__p">
          Retrieval narrows five hundred dictations to a handful of memories. The step that
          matters most is the one after it: if nothing retrieved actually mentions what was
          asked about, Kivi refuses instead of answering from the closest thing it found.
        </p>

        <Figure
          caption="The support check is a vocabulary test, not a model judgement — which is why refusing does not depend on the model behaving well."
          viewBox="0 0 760 168"
          height={168}
        >
          <Box x={4} y={62} w={104} title="Question" />
          <Arrow x1={112} y1={82} x2={148} y2={82} />
          <Box x={152} y={62} w={126} title="Read it" sub="intent + entities" />
          <Arrow x1={282} y1={82} x2={318} y2={82} />
          <Box x={322} y={52} w={140} h={60} title="Search memory" sub="meaning · wording · recency" tone="store" />
          <Arrow x1={466} y1={82} x2={502} y2={82} />
          <Box x={506} y={62} w={124} title="Supported?" tone="decide" />

          {/* refuse */}
          <path d="M568 62 L568 26 L648 26" className="dg__arrow-path" markerEnd="url(#dg-head)" />
          <text x={594} y={19} className="dg__l">no</text>
          <Box x={652} y={8} w={104} h={36} title="Refuse" sub="and say why" tone="warn" />

          {/* answer */}
          <path d="M568 102 L568 138 L648 138" className="dg__arrow-path" markerEnd="url(#dg-head)" />
          <text x={594} y={132} className="dg__l">yes</text>
          <Box x={652} y={120} w={104} h={36} title="Answer" sub="with citations" tone="good" />
        </Figure>
      </section>

      {/* ------------------------------------------------ 4. data model */}
      <section className="how" id="storage">
        <h2 className="how__h">4 · What is actually stored</h2>
        <p className="how__p">
          Four tables carry the whole system. The arrows are the reason any answer can be traced
          back to the words that produced it — and the reason deleting a dictation cannot quietly
          take its audit trail with it.
        </p>

        <Figure
          caption="Every memory points at the dictation it came from; every answer points at the memories it used."
          viewBox="0 0 760 176"
          height={176}
        >
          <Box x={20} y={68} w={140} h={52} title="transcripts" sub="what you said" tone="store" />
          <Arrow x1={164} y1={94} x2={210} y2={94} label="produced" />
          <Box x={214} y={68} w={140} h={52} title="memories" sub="what Kivi knows" tone="store" />
          <Arrow x1={358} y1={94} x2={404} y2={94} label="cited by" />
          <Box x={408} y={68} w={140} h={52} title="query_logs" sub="what it answered" tone="store" />

          <path d="M284 68 L284 30 L404 30" className="dg__arrow-path" markerEnd="url(#dg-head)" />
          <text x={318} y={23} className="dg__l">changes to</text>
          <Box x={408} y={12} w={140} h={36} title="memory_events" sub="every decision, with a reason" tone="muted" />

          <text x={576} y={86} className="dg__s dg__s--left">Statuses:</text>
          <text x={576} y={100} className="dg__s dg__s--left">ACTIVE · SUPERSEDED</text>
          <text x={576} y={114} className="dg__s dg__s--left">REJECTED · DELETED</text>
          <text x={576} y={132} className="dg__s dg__s--left">Nothing is removed.</text>
        </Figure>

        {status ? (
          <div className="how__now">
            <span className="how__now-label">right now</span>
            <Pill tone="good">{active} active</Pill>
            <Pill>{superseded} superseded</Pill>
            <Pill tone="muted">{rejected} rejected</Pill>
            <Pill mono>{status.transcripts} dictations</Pill>
          </div>
        ) : null}
      </section>

      {/* ------------------------------------------------ 5. the algorithms */}
      <section className="how" id="algorithms">
        <h2 className="how__h">5 &middot; The algorithms, and why these ones</h2>
        <p className="how__p">
          Three pieces do the work: turning text into something comparable, deciding which
          memories a question is about, and deciding whether to answer at all.
        </p>

        <h3 className="how__h3">Embeddings &mdash; hashed n-grams, no model</h3>
        <p className="how__p">
          A memory&rsquo;s text is cut into words, word pairs and four-character runs. Each piece
          is hashed into one of 512 buckets, weighted by how rare it is, and the result is
          normalised. No API, no download, no GPU &mdash; and the same text always lands in the
          same place.
        </p>

        <Figure
          caption="Four-character runs are what make it survive typos and word endings: pricing and prices share buckets."
          viewBox="0 0 760 132"
          height={132}
        >
          <Box x={4} y={46} w={132} title="Memory text" sub="what was said" />
          <Arrow x1={140} y1={66} x2={176} y2={66} />
          <Box x={180} y={16} w={126} h={32} title="words" tone="muted" />
          <Box x={180} y={52} w={126} h={32} title="word pairs" tone="muted" />
          <Box x={180} y={88} w={126} h={32} title="4-char runs" tone="muted" />
          <path d="M176 66 L180 32" className="dg__thin" />
          <path d="M176 66 L180 68" className="dg__thin" />
          <path d="M176 66 L180 104" className="dg__thin" />

          <Arrow x1={310} y1={66} x2={346} y2={66} label="blake2b" />
          <Box x={350} y={46} w={140} title="512 buckets" sub="rarity-weighted" tone="store" />
          <Arrow x1={494} y1={66} x2={530} y2={66} />
          <Box x={534} y={46} w={128} title="One vector" sub="comparable" tone="good" />

          <text x={674} y={58} className="dg__s dg__s--left">same text,</text>
          <text x={674} y={70} className="dg__s dg__s--left">same vector,</text>
          <text x={674} y={82} className="dg__s dg__s--left">any machine</text>
        </Figure>

        <p className="how__p">
          The hash is <code>blake2b</code> rather than the language&rsquo;s built-in one, which is
          seeded differently on every run &mdash; the same sentence would land somewhere new after
          a restart and every stored vector would quietly rot. This way a reviewer on another
          machine gets identical numbers.
        </p>

        <h3 className="how__h3">
          Retrieval &mdash; six signals, because each covers the blind spot of the others
        </h3>

        <Figure
          caption="Three signals carry a configurable weight; three are structural bonuses with none. Measured, the bonuses are the larger half."
          viewBox="0 0 760 250"
          height={250}
        >
          <Box x={4} y={104} w={100} title="Question" />

          <text x={140} y={12} className="dg__s dg__s--left">WEIGHTED</text>
          <Box x={140} y={18} w={186} h={32} title="Meaning &middot; 0.55" sub="finds paraphrase" />
          <Box x={140} y={54} w={186} h={32} title="Wording &middot; 0.30" sub="finds names, rare words" />
          <Box x={140} y={90} w={186} h={32} title="Recency &middot; 0.15" sub="breaks ties" />

          <text x={140} y={146} className="dg__s dg__s--left">STRUCTURAL &mdash; NO WEIGHT, ADDED DIRECTLY</text>
          <Box x={140} y={154} w={186} h={26} title="Names a person &middot; +0.40" tone="muted" />
          <Box x={140} y={186} w={186} h={26} title="Right kind of memory" tone="muted" />
          <Box x={140} y={218} w={186} h={26} title="Covers the words &middot; +0.28" tone="muted" />

          <path d="M108 124 L140 34" className="dg__arrow-path" markerEnd="url(#dg-head)" />
          <path d="M108 124 L140 70" className="dg__arrow-path" markerEnd="url(#dg-head)" />
          <path d="M108 124 L140 106" className="dg__arrow-path" markerEnd="url(#dg-head)" />
          <path d="M108 124 L140 167" className="dg__thin" />
          <path d="M108 124 L140 199" className="dg__thin" />
          <path d="M108 124 L140 231" className="dg__thin" />

          <path d="M330 34 L366 124" className="dg__thin" />
          <path d="M330 70 L366 124" className="dg__thin" />
          <path d="M330 106 L366 124" className="dg__thin" />
          <path d="M330 167 L366 124" className="dg__thin" />
          <path d="M330 199 L366 124" className="dg__thin" />
          <path d="M330 231 L366 124" className="dg__thin" />

          <Box x={370} y={104} w={112} title="One score" tone="decide" />
          <text x={504} y={96} className="dg__l">superseded?</text>
          <Arrow x1={486} y1={124} x2={518} y2={124} />
          <Box x={522} y={104} w={118} title="&times; 0.45" sub="demoted, not cut" tone="warn" />
          <Arrow x1={644} y1={124} x2={676} y2={124} />
          <Box x={680} y={104} w={64} title="Top 8" tone="good" />
        </Figure>

        <p className="how__p">
          The weights are the visible part and the smaller part. Naming a person adds up to
          0.40 outright; a typical meaning score of 0.4 contributes 0.55 &times; 0.4, about
          0.22. Rather than argue that from the source, every question stores its full ranking
          per signal, so the system can be asked directly:
        </p>

        <SignalTable />

        <p className="how__p">
          Meaning-matching finds a paraphrase but blurs proper nouns; word-matching is what
          rescues a rare name like <em>Rahul</em>, which meaning-matching treats as one dimension
          among five hundred. Recency settles ties. But on the questions this installation has
          actually been asked, the structural signals decide more of the ranking than the
          weighted ones do &mdash; which is a fact about these questions, not a law: a corpus of
          questions that all name a person will find the entity bonus dominant, because it is.
        </p>

        <h3 className="how__h3">Refusing &mdash; a vocabulary check, deliberately not a judgement</h3>
        <p className="how__p">
          Word-matching always returns <em>something</em>. Ask for a bank account number and it
          will happily hand back whichever dictation shares the most ordinary words. So before any
          answer is written, Kivi checks a separate question: does the topic of the question appear
          anywhere in what was retrieved?
        </p>

        <Figure
          caption="This is why Kivi refusing does not depend on a model behaving well. The check is ordinary code, and it runs whichever engine is configured."
          viewBox="0 0 760 130"
          height={130}
        >
          <Box x={4} y={44} w={150} title="A question" sub="topic: phone, number" />
          <Arrow x1={158} y1={64} x2={194} y2={64} />
          <Box x={198} y={44} w={160} title="Top 8 memories" sub="best available match" tone="store" />
          <Arrow x1={362} y1={64} x2={398} y2={64} />
          <Box x={402} y={34} w={168} h={60} title="Does the topic appear" sub="in any of them?" tone="decide" />

          <path d="M486 34 L486 12 L594 12" className="dg__arrow-path" markerEnd="url(#dg-head)" />
          <text x={520} y={6} className="dg__l">no</text>
          <Box x={598} y={0} w={158} h={34} title="Refuse" sub="whatever the score said" tone="warn" />

          <path d="M486 94 L486 112 L594 112" className="dg__arrow-path" markerEnd="url(#dg-head)" />
          <text x={520} y={106} className="dg__l">yes</text>
          <Box x={598} y={94} w={158} h={34} title="Answer" sub="cite what supported it" tone="good" />
        </Figure>

        <h3 className="how__h3">Where a model fits &mdash; and where it deliberately does not</h3>
        <p className="how__p">
          Everything above runs without one. A model, when configured, is used in exactly three
          places and nowhere else.
        </p>

        <Figure
          caption="The pieces a model touches are the judgements. The pieces that decide whether to answer at all are not."
          viewBox="0 0 760 156"
          height={156}
        >
          <text x={8} y={16} className="dg__s dg__s--left">A MODEL DECIDES</text>
          <Box x={8} y={26} w={168} h={38} title="Extract" sub="what is worth keeping" tone="good" />
          <Box x={188} y={26} w={168} h={38} title="Reconcile" sub="new / duplicate / replaces" tone="good" />
          <Box x={368} y={26} w={168} h={38} title="Compose" sub="wording of the answer" tone="good" />

          <text x={8} y={96} className="dg__s dg__s--left">CODE DECIDES</text>
          <Box x={8} y={106} w={140} h={38} title="Embed" sub="hashed n-grams" tone="store" />
          <Box x={160} y={106} w={140} h={38} title="Rank" sub="the three scores" tone="store" />
          <Box x={312} y={106} w={168} h={38} title="Refuse or answer" sub="the support check" tone="store" />
          <Box x={492} y={106} w={140} h={38} title="Store" sub="statuses, audit" tone="store" />

          <text x={556} y={40} className="dg__s dg__s--left">swap the model,</text>
          <text x={556} y={52} className="dg__s dg__s--left">these change</text>
          <text x={652} y={98} className="dg__s dg__s--left">these do not</text>
        </Figure>

        <p className="how__p">
          The split is the point. If a model also decided whether an answer was supported, then
          &ldquo;Kivi does not invent answers&rdquo; would be a claim about that model on that day.
          Because the check is ordinary code, the guarantee holds whichever engine is configured
          &mdash; and it is why the offline engine and a hosted model produce the same refusals
          from the same history.
        </p>

        <p className="how__p">
          What a model does change is how much gets learned in the first place. On dictations
          phrased in a voice the rules were never tuned on, extraction recall goes from 62% to
          97%. Retrieval and embedding are identical in both paths, so that entire gap is
          extraction &mdash; which is the honest answer to what the model is for.
        </p>

        <div className="how__grid" style={{ marginTop: 16 }}>
          <div className="how__col">
            <div className="how__col-head how__col-head--yes">Why it is built this way</div>
            <ul className="how__list">
              <li>
                The refusal gate is ordinary code, so &ldquo;does not invent answers&rdquo; is a
                property of the system rather than of a model having a good day.
              </li>
              <li>
                Retrieval and embedding are identical whichever engine is configured, so the
                measured quality gap between rules and a model sits entirely in extraction &mdash;
                62% against 97% recall on unseen phrasing.
              </li>
              <li>
                Nothing is deleted anywhere in the scoring path. A correction lowers a score; it
                never removes a row.
              </li>
            </ul>
          </div>
          <div className="how__col">
            <div className="how__col-head how__col-head--no">What it costs</div>
            <ul className="how__list">
              <li>
                Hashed embeddings match words and shapes, not concepts. A paraphrase sharing no
                vocabulary is missed &mdash; the cause of both evaluation failures.
              </li>
              <li>
                Every question rebuilds the word index and compares every vector. Milliseconds for
                one person&rsquo;s history; it would need a real index at a much larger scale.
              </li>
              <li>
                People and projects are recognised by capitalisation. Two colleagues sharing a
                first name would merge.
              </li>
            </ul>
          </div>
        </div>
      </section>

      {/* --------------------------------- 6. would a vector database help? */}
      <section className="how" id="vectordb">
        <h2 className="how__h">6 &middot; Would a vector database help?</h2>
        <p className="how__p">
          It is the first question anyone asks about a retrieval system, so it is worth
          answering with measurements rather than an opinion. Kivi is already
          retrieval-augmented generation &mdash; a question retrieves memories, the
          memories are handed to the engine, the engine writes from them and nothing
          else. What FAISS or a hosted vector database would replace is the <em>index</em>:
          how the nearest vectors are found, not how well they answer.
        </p>

        <p className="how__p">
          Kivi scans every vector on every question. No index, no shortlist, no
          approximation. Here is what that costs on this installation:
        </p>

        <Figure
          caption="An approximate index is a trade: some recall for a lot of speed. At this size there is no speed left to buy, and the scan is already exact."
          viewBox="0 0 760 168"
          height={168}
        >
          <text x={8} y={14} className="dg__s dg__s--left">
            SEARCHING 402 MEMORIES &times; 512 DIMENSIONS
          </text>

          <Box x={8} y={26} w={210} h={40} title="What Kivi does now" sub="exact scan, plain Python" />
          <rect x={228} y={36} width={430} height={20} rx={3} className="dg__fill-warn" />
          <text x={668} y={51} className="dg__l dg__l--left">123 ms</text>

          <Box x={8} y={80} w={210} h={40} title="The same scan, vectorised" sub="one numpy matmul" tone="good" />
          <rect x={228} y={90} width={4} height={20} rx={2} className="dg__fill-good" />
          <text x={240} y={105} className="dg__l dg__l--left">0.13 ms &mdash; still exact, still every vector</text>

          <Box x={8} y={134} w={210} h={26} title="An approximate index" tone="muted" />
          <text x={240} y={151} className="dg__l dg__l--left">
            starts paying off somewhere around 100,000 vectors
          </text>
        </Figure>

        <p className="how__p">
          So the honest answer is <b>no, and not for the reason people expect</b>. The exact
          search over the whole corpus is already fast enough that an approximate index has
          nothing to offer, and one line of vectorised arithmetic beats the current loop by
          three orders of magnitude without giving up exactness. FAISS solves a problem that
          begins a few hundred thousand memories from here &mdash; decades of dictation.
        </p>

        <p className="how__p">
          The scale it sits at matters more than the ratio. A question against a hosted model
          takes about ten seconds end to end, of which the vector scan is roughly one percent.
          Replacing it saves a hundredth of the wait. And because an approximate index buys
          speed by giving up recall, it could only move the number that actually matters &mdash;
          how often the right memory is retrieved &mdash; in the wrong direction.
        </p>

        <h3 className="how__h3">What would help is the embedding, not the index</h3>
        <p className="how__p">
          The blind spot is not <em>finding</em> the nearest vector. It is that nearest is
          measured over hashed words, word pairs and character runs, so two sentences that
          mean the same thing in different vocabulary are not near each other at all. Both
          failures in the evaluation are exactly that: a question phrased with none of the
          words the memory used. A real sentence-embedding model closes that gap, and no index
          ever will.
        </p>

        <div className="how__grid" style={{ marginTop: 16 }}>
          <div className="how__col">
            <div className="how__col-head how__col-head--yes">Why it is not done here</div>
            <ul className="how__list">
              <li>
                A sentence-embedding model is a download. The current promise is that this
                repository clones, runs and indexes five hundred dictations offline in seconds,
                with nothing fetched.
              </li>
              <li>
                Meaning is 20% of what decides the ranking on the questions asked so far.
                Improving it improves a fifth of the score.
              </li>
              <li>
                Naming a person is the largest signal and the crudest implementation &mdash;
                capitalisation and a substring match. That is the cheaper fix and the bigger
                one.
              </li>
            </ul>
          </div>
          <div className="how__col">
            <div className="how__col-head how__col-head--no">What would change at scale</div>
            <ul className="how__list">
              <li>
                Around 10,000 memories the scan wants vectorising. That is a dependency, not a
                database.
              </li>
              <li>
                Rebuilding the BM25 index per question becomes the bottleneck before vector
                search does &mdash; it is the same full pass, over the same corpus, with worse
                constants.
              </li>
              <li>
                Past a few hundred thousand, an approximate index earns its place. By then the
                interesting question is sharding by user, which SQLite would also have stopped
                answering well.
              </li>
            </ul>
          </div>
        </div>
      </section>


      {/* --------------------------------------- 7. the advanced techniques */}
      <section className="how" id="advanced">
        <h2 className="how__h">7 &middot; Techniques not used, and why</h2>
        <p className="how__p">
          Retrieval has a well-known toolbox above what is built here &mdash; fusion,
          reranking, learned embeddings, query rewriting, graph memory. Leaving them out is
          a decision, and a decision is only defensible if you can say what it cost. Each of
          these was measured against this system rather than judged in the abstract, and two
          of them would help.
        </p>

        <div className="adv">
          <div className="adv__row adv__row--head">
            <span>Technique</span>
            <span>What it is for</span>
            <span>Here</span>
          </div>

          <div className="adv__row">
            <span className="adv__name">
              Reciprocal Rank Fusion
              <em>combine rankings by position, not score</em>
            </span>
            <span className="adv__what">
              Merging retrievers whose scores are on scales that cannot be compared &mdash;
              a cosine of 0.42 against a BM25 of 11.7. RRF ignores the numbers and uses only
              the rank each retriever gave.
            </span>
            <span className="adv__verdict adv__verdict--no">
              <b>Solves a problem this code does not have.</b> Both signals are already
              divided by the best score in the same question, so they arrive on a common
              0&ndash;1 scale. RRF would earn its place the moment a third retriever with an
              incomparable score is added &mdash; not before.
            </span>
          </div>

          <div className="adv__row">
            <span className="adv__name">
              Cross-encoder reranking
              <em>re-score the shortlist, question and memory read together</em>
            </span>
            <span className="adv__what">
              A first pass retrieves generously; a slower model then reads each candidate
              beside the question and reorders. It is the standard way to buy precision at
              the top of a list.
            </span>
            <span className="adv__verdict adv__verdict--no">
              <b>Nothing here for it to fix.</b> Reranking can only reorder what was
              retrieved, and the right memory is in the retrieved set 96.4% of the time. In
              the one evaluation case that misses a memory, the expected dictation was never
              retrieved at all &mdash; a reranker would have reordered the same wrong eight.
              No failure in the suite is a case of the right memory being retrieved and then
              ranked below the cut.
            </span>
          </div>

          <div className="adv__row">
            <span className="adv__name">
              Learned sentence embeddings
              <em>a trained model instead of hashed n-grams</em>
            </span>
            <span className="adv__what">
              Maps sentences into a space where <em>the deadline slipped</em> and
              <em> we are running late on delivery</em> land near each other despite sharing
              no words. The gap hashing cannot close.
            </span>
            <span className="adv__verdict adv__verdict--yes">
              <b>The one that would move the number.</b> Both failures are paraphrases with
              no shared vocabulary &mdash; exactly what this fixes. The cost is a model
              download, which is the whole reason it is not here: today the repository clones
              and indexes five hundred dictations offline, fetching nothing.
            </span>
          </div>

          <div className="adv__row">
            <span className="adv__name">
              Hypothetical document embeddings
              <em>search with an invented answer, not the question</em>
            </span>
            <span className="adv__what">
              A question and its answer are written differently, which is half of why
              similarity misses. So have the model draft the answer it expects, embed
              <em> that</em>, and search with it &mdash; matching a statement against
              statements.
            </span>
            <span className="adv__verdict adv__verdict--maybe">
              <b>Attacks the same gap without a download.</b> The catch is that it needs a
              model on the question path, so it cannot run offline, and it adds a whole
              round-trip to a question that already spends 98% of its time waiting for one.
            </span>
          </div>

          <div className="adv__row">
            <span className="adv__name">
              Entity resolution
              <em>knowing that Priya, Priya S. and she are one person</em>
            </span>
            <span className="adv__what">
              Proper handling of aliases, nicknames, initials and pronouns, so a name in a
              question reaches every memory about that person rather than the ones that spell
              it the same way.
            </span>
            <span className="adv__verdict adv__verdict--yes">
              <b>Largest signal, crudest implementation.</b> Naming someone is worth up to
              +0.40 &mdash; among the biggest contributions in the measured table above
              &mdash; and it is decided by capitalisation and a substring match. Two
              colleagues sharing a first name merge; a nickname finds nothing.
            </span>
          </div>

          <div className="adv__row">
            <span className="adv__name">
              Graph memory
              <em>entities and relations, not just documents</em>
            </span>
            <span className="adv__what">
              Store memories as a graph of entities joined by typed, time-stamped edges, so a
              question can be answered by traversal &mdash; who reports to whom, what changed
              when &mdash; rather than by similarity alone.
            </span>
            <span className="adv__verdict adv__verdict--maybe">
              <b>Half-built already, and the natural direction.</b> Memories are linked by
              supersession and by contradiction, and every one carries both when it happened
              and when it was learned. What is missing is traversal at query time: nothing
              currently walks those edges to answer.
            </span>
          </div>
        </div>

        <h3 className="how__h3">What that adds up to</h3>
        <p className="how__p">
          The two rejections are rejected by the same measurement, and it is worth being exact
          about it. Fifty of fifty-two cases pass. Neither failure is an ordering failure:
        </p>

        <p className="how__p">
          <b>&ldquo;How do I prefer my meeting summaries?&rdquo;</b> Kivi answers with two
          true preferences about summaries and misses the one the case asks for. The dictation
          holding it was <em>never retrieved</em> &mdash; not retrieved and ranked low,
          absent from the eight. Reranking those eight could not have reached it. An embedding
          that put <em>bullet points</em> near <em>how do I prefer my summaries</em> could.
        </p>

        <p className="how__p">
          <b>&ldquo;When is the Atlas pricing sign-off with Sarah?&rdquo;</b> Two live times
          exist and Kivi confidently gives one. This is not a retrieval failure at all: both
          were stored, and the conflict was never <em>detected</em>, because reconciliation
          groups candidates by shared words and the two dictations phrase the same appointment
          differently. The same weakness as the first &mdash; words standing in for meaning
          &mdash; but at write time rather than at query time.
        </p>

        <p className="how__p">
          So one failure is a query-side embedding problem and the other is a write-side one.
          Fusion and reranking address neither. That is the whole argument for spending the
          effort on what things mean rather than on how candidates are ordered.
        </p>

        <Figure
          caption="Reranking works on the band where the right memory was retrieved. Neither failure is in that band."
          viewBox="0 0 760 168"
          height={168}
        >
          <text x={8} y={14} className="dg__s dg__s--left">
            52 EVALUATION CASES
          </text>
          <rect x={8} y={24} width={713} height={22} rx={3} className="dg__fill-good" />
          <rect x={723} y={24} width={29} height={22} rx={3} className="dg__fill-warn" />
          <text x={8} y={62} className="dg__l dg__l--left">
            50 pass &mdash; and everything a reranker could improve is already inside this
          </text>
          <text x={716} y={62} className="dg__l dg__l--left">2 fail</text>

          <text x={8} y={100} className="dg__s dg__s--left">WHAT THOSE TWO ACTUALLY ARE</text>
          <Box
            x={8}
            y={112}
            w={230}
            h={44}
            title="Never retrieved"
            sub="query-side: no shared words"
            tone="warn"
          />
          <Box
            x={250}
            y={112}
            w={230}
            h={44}
            title="Conflict never detected"
            sub="write-side: no shared words"
            tone="warn"
          />
          <Box
            x={492}
            y={112}
            w={260}
            h={44}
            title="Retrieved, then ranked too low"
            sub="what reranking fixes — 0 cases"
            tone="muted"
          />
        </Figure>

        <p className="how__p">
          So the order of work is not the order the literature is usually read in. First
          entity resolution, because it is the largest signal and the weakest code and it needs
          no model at all. Then learned embeddings, accepting the download, because that is the
          only thing that reaches the failures. Reranking and fusion come after that &mdash; not
          because they are bad, but because on this corpus there is nothing left for them to
          fix.
        </p>

        <p className="how__p how__p--last">
          And none of it should be shipped on an argument. The way to know is the evaluation
          already in this repository: 52 cases, failures shown first, run with{" "}
          <code>python evaluation/run_eval.py</code>. Any of these is worth taking only if that
          number moves.
        </p>
      </section>


    </div>
  );
}
