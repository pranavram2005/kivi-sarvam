/** Small shared pieces, so each screen stays about its own behaviour. */

export function Pill({ tone = "default", mono = false, children, title }) {
  const tones = {
    default: "",
    good: " pill--good",
    warn: " pill--warn",
    rose: " pill--rose",
    muted: " pill--muted",
  };
  return (
    <span className={`pill${tones[tone] || ""}${mono ? " pill--mono" : ""}`} title={title}>
      {children}
    </span>
  );
}

export function Spinner() {
  return <span className="spinner" aria-label="Loading" />;
}

export function Empty({ title, children }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children ? <p className="small">{children}</p> : null}
    </div>
  );
}

export function ErrorBanner({ error, onRetry }) {
  if (!error) return null;
  return (
    <div className="banner row row--between" role="alert">
      <span>{String(error.message || error)}</span>
      {onRetry ? (
        <button className="btn btn--ghost btn--small" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}

/**
 * Splits the opening word out of a string title so it can wear Kivi's marker
 * swipe. Only a plain string is split — a title passed as JSX is already making
 * its own typographic decisions, and second-guessing it would fight the caller.
 */
function markFirstWord(title) {
  if (typeof title !== "string") return title;
  // Match letters only, so "Ask, and see why" highlights "Ask" and leaves the
  // comma outside — a swipe drawn over its own punctuation looks like a mistake.
  const m = /^[\p{L}\p{N}'’-]+/u.exec(title);
  if (!m) return title;
  return (
    <>
      <mark>{m[0]}</mark>
      {title.slice(m[0].length)}
    </>
  );
}

export function PageHead({ eyebrow, title, lede, children }) {
  return (
    <header className="page__head">
      {eyebrow ? <div className="page__eyebrow">{eyebrow}</div> : null}
      <h1 className="page__title">{markFirstWord(title)}</h1>
      {lede ? <p className="page__lede">{lede}</p> : null}
      {children}
    </header>
  );
}

/** The status of a memory, said in words a person would use. */
export function StatusPill({ status }) {
  const map = {
    ACTIVE: ["good", "Current"],
    SUPERSEDED: ["warn", "Replaced"],
    DELETED: ["rose", "Forgotten"],
    REJECTED: ["muted", "Not trusted"],
  };
  const [tone, label] = map[status] || ["muted", status];
  return <Pill tone={tone}>{label}</Pill>;
}

const TYPE_LABELS = {
  fact: "Fact",
  preference: "Preference",
  episode: "Discussion",
  task: "Commitment",
  event: "Scheduled",
};

export function TypePill({ type }) {
  return <Pill>{TYPE_LABELS[type] || type}</Pill>;
}

export function typeLabel(type) {
  return TYPE_LABELS[type] || type;
}
