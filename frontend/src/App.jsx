/**
 * App shell.
 *
 * A quiet rail down the left holding the four screens, with the current state
 * of the system at its foot so a reviewer can see at a glance which engine is
 * running and how much Kivi has learned. Routing is a single piece of state -
 * four screens do not need a router.
 */

import { useCallback, useEffect, useState } from "react";
import History from "./pages/History";
import HeyKivi from "./pages/HeyKivi";
import Knowledge from "./pages/Knowledge";
import Inspector from "./pages/Inspector";
import { api } from "./services/api";
import { ErrorBanner } from "./components/ui";

/**
 * Line icons at 16px on a 1.5 stroke, matching the Kivi desktop app's rail.
 * Drawn inline rather than pulled from an icon font so they inherit `color`
 * and go green on the current item without a second asset to keep in sync.
 */
const Icon = {
  clock: (
    <>
      <circle cx="8" cy="8" r="6.25" />
      <path d="M8 4.5V8l2.4 1.4" />
    </>
  ),
  spark: <path d="M8 1.75l1.6 4.65 4.65 1.6-4.65 1.6L8 14.25l-1.6-4.65L1.75 8l4.65-1.6z" />,
  layers: (
    <>
      <path d="M8 1.9l6 3.1-6 3.1-6-3.1z" />
      <path d="M2 8.6l6 3.1 6-3.1" />
    </>
  ),
  list: (
    <>
      <path d="M2.2 4.2h11.6M2.2 8h11.6M2.2 11.8h7.2" />
    </>
  ),
};

const SCREENS = [
  { key: "history", label: "history", icon: "clock", hint: "your dictations" },
  { key: "kivi", label: "hey kivi", icon: "spark", hint: "ask a question" },
  { key: "knows", label: "memory", icon: "layers", hint: "what Kivi knows" },
  { key: "inspect", label: "inspector", icon: "list", hint: "evidence and evaluation" },
];

const KEYS = SCREENS.map((s) => s.key);

function screenFromHash() {
  // "#/kivi?q=..." -> "kivi": the screen key is everything before the query.
  const key = window.location.hash.replace(/^#\/?/, "").split("?")[0];
  return KEYS.includes(key) ? key : "kivi";
}

export default function App() {
  // Screens live in the URL hash so a reviewer can link straight to one, and
  // the browser's back button behaves the way people expect. Four screens do
  // not justify a router.
  const [screen, setScreenState] = useState(screenFromHash);
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  // The rail collapses, the way Kivi's does. Remembered per browser, because
  // whether you want the navigation visible is a standing preference rather
  // than a per-visit one. Storage can throw in a private window, so it is
  // wrapped and the app opens with the rail showing if it cannot be read.
  const [railOpen, setRailOpen] = useState(() => {
    try {
      return window.localStorage.getItem("kivi.rail") !== "closed";
    } catch {
      return true;
    }
  });

  const toggleRail = useCallback(() => {
    setRailOpen((open) => {
      const next = !open;
      try {
        window.localStorage.setItem("kivi.rail", next ? "open" : "closed");
      } catch {
        /* the preference simply will not persist */
      }
      return next;
    });
  }, []);

  const setScreen = useCallback((key) => {
    window.location.hash = `#/${key}`;
    setScreenState(key);
  }, []);

  useEffect(() => {
    const onHashChange = () => setScreenState(screenFromHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const refresh = useCallback(async () => {
    try {
      setStatus(await api.status());
      setError(null);
    } catch (err) {
      setError(err);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const counts = {
    history: status?.transcripts,
    knows: status?.memories?.ACTIVE,
    inspect: status?.queries,
  };

  return (
    <div className={`app${railOpen ? "" : " app--rail-closed"}`}>
      <button
        className="rail-toggle"
        onClick={toggleRail}
        aria-expanded={railOpen}
        aria-label={railOpen ? "Hide navigation" : "Show navigation"}
        title={railOpen ? "Hide navigation" : "Show navigation"}
      >
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"
             strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <rect x="1.75" y="2.75" width="12.5" height="10.5" rx="2" />
          <path d="M6.25 2.75v10.5" />
        </svg>
      </button>

      <aside className="rail" inert={railOpen ? undefined : ""}>
        <div>
          <div className="wordmark">
            {/* Dotless "i" (U+0131) twice, so the tittles can be drawn as green
                dots rather than inherited from the typeface in text colour.
                This is Kivi's mark: the word in the serif, the two dots green. */}
            <span className="wordmark__name" aria-label="kivi">
              k<i className="tittle">&#x131;</i>
              <span className="no-narrow">
                v<i className="tittle">&#x131;</i>
              </span>
            </span>
          </div>
          <span className="wordmark__sub">you talk. kivi remembers.</span>
        </div>

        <nav className="nav">
          {SCREENS.map((item) => (
            <button
              key={item.key}
              className="nav__item"
              aria-current={screen === item.key ? "page" : undefined}
              onClick={() => setScreen(item.key)}
              title={item.hint}
            >
              <span className="nav__glyph" aria-hidden="true">
                <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"
                     strokeLinecap="round" strokeLinejoin="round">
                  {Icon[item.icon]}
                </svg>
              </span>
              <span className="nav__label">{item.label}</span>
              {counts[item.key] !== undefined && counts[item.key] !== null ? (
                <span className="nav__count">{counts[item.key]}</span>
              ) : null}
            </button>
          ))}
        </nav>

        <div className="rail__foot">
          {status ? (
            <div className="engine-chip" title="the engine answering, and what it holds">
              {/* Kivi puts the person here; we put the engine, in the same
                  shape - a round mark, a name, a second line beneath. It is
                  the standing answer to "who is replying to me right now". */}
              <span className="engine-chip__mark" aria-hidden="true">
                {(status.llm_provider || "?").charAt(0)}
              </span>
              <span className="engine-chip__text">
                <span className="engine-chip__name" title={status.llm_model}>
                  {status.llm_provider}
                </span>
                <span className="engine-chip__sub">
                  {status.memories?.ACTIVE || 0} live
                  {status.memories?.SUPERSEDED ? ` · ${status.memories.SUPERSEDED} old` : ""}
                </span>
              </span>
              {status.transcripts_unprocessed ? (
                <span className="engine-chip__pending" title="dictations not yet processed">
                  {status.transcripts_unprocessed}
                </span>
              ) : null}
            </div>
          ) : null}
        </div>
      </aside>

      <main className="main">
        {error ? (
          <div style={{ maxWidth: "var(--page-max)", margin: "0 auto 24px" }}>
            <ErrorBanner error={error} onRetry={refresh} />
          </div>
        ) : null}

        {screen === "history" ? (
          <History onRefresh={refresh} />
        ) : screen === "kivi" ? (
          <HeyKivi onRefresh={refresh} />
        ) : screen === "knows" ? (
          <Knowledge onRefresh={refresh} />
        ) : (
          <Inspector status={status} />
        )}
      </main>
    </div>
  );
}
