/**
 * How the system works, in four diagrams.
 *
 * This screen exists because the assignment asks that "an engineer can inspect
 * why memory did or did not affect a result", and a reader who has just opened
 * the app has no way to know what happens between speaking and being answered.
 * The Inspector shows evidence for one particular answer; this shows the shape
 * of the machine that produced it.
 *
 * The diagrams are hand-drawn SVG rather than a rendered diagram language. They
 * are four fixed pictures, not generated ones, so a library would be weight
 * without benefit - and inline paths inherit `currentColor`, which is what lets
 * them work in both themes without a second asset.
 */
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
        lede="Four pictures of the same system: what happens to a dictation, how Kivi decides whether it already knew something, what a question goes through, and what is actually stored."
      />

      {/* ------------------------------------------------ 1. ingest */}
      <section className="how">
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
      <section className="how">
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
      <section className="how">
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
      <section className="how">
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

      {/* ------------------------------------------------ roadmap */}
      <section className="how">
        <h2 className="how__h">Built, and not built</h2>
        <p className="how__p">
          The assignment asks for a system narrow enough to finish and complete enough to
          interrogate. This is where that line was drawn.
        </p>

        <div className="how__grid">
          <div className="how__col">
            <div className="how__col-head how__col-head--yes">Built</div>
            <ul className="how__list">
              <li>Five memory types, extracted and typed from plain dictation</li>
              <li>Deliberate ignoring, with the reason recorded</li>
              <li>Corrections that supersede without destroying</li>
              <li>Conflicts surfaced rather than silently resolved</li>
              <li>Refusal when the history does not support an answer</li>
              <li>Provenance from any answer back to the words spoken</li>
              <li>Correct, forget, restore, and delete a dictation</li>
              <li>A reproducible evaluation over the whole pipeline</li>
            </ul>
          </div>

          <div className="how__col">
            <div className="how__col-head how__col-head--no">Not built</div>
            <ul className="how__list">
              <li>Accounts and sign-in — one user, no authentication</li>
              <li>Separating personal context from work context</li>
              <li>Organisation policy and administrator controls</li>
              <li>Finding a dictation by time or application</li>
              <li>Identity resolution — two people sharing a first name merge</li>
              <li>Pacing requests to a model's rate limit</li>
            </ul>
            <p className="how__note">
              Each of these was left out because no chosen use case required it — not because it
              was overlooked. The README says the same, at length.
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}
