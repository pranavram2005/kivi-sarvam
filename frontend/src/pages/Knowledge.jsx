/**
 * Screen 3 — What Kivi Knows.
 *
 * Kivi's current understanding, arranged the way a person thinks about their
 * work: the people, the projects, what's coming up, what they owe, and how they
 * like things written.
 *
 * Deliberately absent from this screen: memory ids in the reading flow,
 * embeddings, vector dimensions, confidence scores, retrieval internals. A user
 * correcting a wrong fact about a colleague should not have to think about a
 * database. Every one of those details is still available — one screen over, in
 * the Inspector, where it belongs.
 *
 * Every memory here can be edited, corrected or forgotten. Forgetting is
 * reversible: the memory moves to a "forgotten" state rather than vanishing.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, formatStamp } from "../services/api";
import { Empty, ErrorBanner, PageHead, Pill, Spinner, StatusPill, TypePill } from "../components/ui";
import { Figure, Figures, StatsStrip } from "../components/PageStats";
import { AreaTrend, BarList, CompositionBar, SERIES, STATUS_COLOR, statusLabel, typeLabel } from "../components/charts";

const TABS = [
  { key: "people", label: "People" },
  { key: "projects", label: "Projects" },
  { key: "upcoming", label: "Coming up" },
  { key: "commitments", label: "You owe" },
  { key: "preferences", label: "How you like things" },
  { key: "archive", label: "Replaced & forgotten" },
];

export default function Knowledge({ onRefresh }) {
  const [view, setView] = useState(null);
  const [archive, setArchive] = useState([]);
  // The tab is readable from the URL: #/knows?tab=upcoming — so a particular
  // view can be linked to, and a reload keeps you where you were.
  const [tab, setTab] = useState(() => {
    const query = window.location.hash.split("?")[1];
    const wanted = query ? new URLSearchParams(query).get("tab") : null;
    return TABS.some((t) => t.key === wanted) ? wanted : "people";
  });
  const [openGroup, setOpenGroup] = useState(null);
  // Stable, so the modal's key/scroll-lock effect binds once instead of
  // re-binding on every parent render.
  const closeModal = useCallback(() => setOpenGroup(null), []);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [knowledge, older] = await Promise.all([
        api.knowledge(),
        api.memories({ status: ["SUPERSEDED", "DELETED", "REJECTED"], limit: 300 }),
      ]);
      setView(knowledge);
      setArchive(older);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  // A person or project can be deep-linked: #/knows?open=Kenji
  useEffect(() => {
    if (!view || openGroup) return;
    const query = window.location.hash.split("?")[1];
    const key = query ? new URLSearchParams(query).get("open") : null;
    if (!key) return;
    const match = [...(view.people || []), ...(view.projects || [])].find(
      (g) => g.key.toLowerCase() === key.toLowerCase(),
    );
    if (match) setOpenGroup(match);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

  async function mutate(action) {
    try {
      await action();
      await load();
      onRefresh?.();
    } catch (err) {
      setError(err);
    }
  }

  if (loading) {
    return (
      <div className="page">
        <div className="row muted" style={{ padding: "40px 0" }}>
          <Spinner /> Reading Kivi's memory…
        </div>
      </div>
    );
  }

  const counts = view?.counts || {};

  return (
    <div className="page">
      <PageHead
        eyebrow="screen 3 · what kivi knows"
        title="What Kivi has understood"
        lede="Not a transcript — an understanding. This is everything Kivi currently believes about your work, in your language rather than the database's. Correct anything that's wrong; forget anything it shouldn't have kept."
      >
        <div className="row row--wrap" style={{ marginTop: 16 }}>
          <Pill tone="good">{counts.ACTIVE || 0} current</Pill>
          <Pill tone="warn">{counts.SUPERSEDED || 0} replaced</Pill>
          <Pill tone="muted">{counts.REJECTED || 0} not trusted</Pill>
          {counts.DELETED ? <Pill tone="rose">{counts.DELETED} forgotten</Pill> : null}
        </div>
      </PageHead>

      <ErrorBanner error={error} onRetry={load} />

      <StatsStrip
        always
        title="What Kivi believes, in numbers"
        load={api.memoryAnalytics}
        render={(d) => {
          const typeOrder = ["fact", "event", "task", "preference", "episode"];
          const typeRows = typeOrder
            .map((k) => d.by_type.find((r) => r.key === k) || { key: k, count: 0 })
            .filter((r) => r.count > 0);
          const statusOrder = ["ACTIVE", "SUPERSEDED", "REJECTED", "DELETED"];
          const statusRows = statusOrder
            .map((k) => d.by_status.find((r) => r.key === k) || { key: k, count: 0 })
            .filter((r) => r.count > 0);
          return (
            <>
              <Figures>
                <Figure label="current" value={d.summary.active.toLocaleString()} tone="good" />
                <Figure label="replaced" value={d.summary.superseded} tone="warn" note="kept, not deleted" />
                <Figure label="not trusted" value={d.summary.rejected} note="below the threshold" />
                <Figure label="people" value={d.summary.people} />
                <Figure label="projects" value={d.summary.projects} />
                <Figure label="duplicates skipped" value={d.summary.duplicates_skipped} />
              </Figures>

              <div className="strip__charts">
                <div>
                  <div className="strip__chart-title">What kind of memory</div>
                  <CompositionBar
                    rows={typeRows}
                    colorFor={(k) => SERIES[typeOrder.indexOf(k) % SERIES.length]}
                    labelFor={typeLabel}
                  />
                </div>
                <div>
                  <div className="strip__chart-title">What Kivi still believes</div>
                  <CompositionBar
                    rows={statusRows}
                    colorFor={(k) => STATUS_COLOR[k] || "#7c7a6e"}
                    labelFor={statusLabel}
                  />
                </div>
              </div>

              <div className="strip__charts">
                <div>
                  <div className="strip__chart-title">Who Kivi knows about</div>
                  <BarList rows={d.top_people} formatNote={(r) => `${r.count} memories`} />
                </div>
                <div>
                  <div className="strip__chart-title">Memory built over time</div>
                  <AreaTrend points={d.weekly} xKey="week" yKey="cumulative" label="memories" height={150} />
                </div>
              </div>
            </>
          );
        }}
      />

      <div className="tabs" role="tablist">
        {TABS.map((item) => {
          const size =
            item.key === "archive"
              ? archive.length
              : Array.isArray(view?.[item.key])
                ? view[item.key].length
                : 0;
          return (
            <button
              key={item.key}
              role="tab"
              aria-selected={tab === item.key}
              className="tab"
              onClick={() => setTab(item.key)}
            >
              {item.label} {size ? <span className="mono tiny">{size}</span> : null}
            </button>
          );
        })}
      </div>

      {tab === "people" || tab === "projects" ? (
        <GroupGrid groups={view?.[tab] || []} onMutate={mutate} kind={tab} onOpen={setOpenGroup} />
      ) : tab === "archive" ? (
        <ArchiveList memories={archive} onMutate={mutate} />
      ) : (
        <FlatList
          memories={view?.[tab] || []}
          onMutate={mutate}
          byDay={tab === "upcoming"}
          emptyTitle={
            tab === "upcoming"
              ? "Nothing scheduled"
              : tab === "commitments"
                ? "Nothing outstanding"
                : "No preferences learned yet"
          }
          emptyBody={
            tab === "upcoming"
              ? "Meetings and deadlines Kivi hears about in your dictations appear here, grouped by day."
              : tab === "commitments"
                ? "When you say you owe someone something, Kivi keeps it here until you correct or forget it."
                : "Kivi learns how you like things written from the way you talk about your own work."
          }
        />
      )}

      {openGroup ? (
        <GroupModal
          group={
            // re-read from the freshly loaded view so an edit made inside the
            // modal is reflected without closing it
            (view?.people || []).concat(view?.projects || []).find((g) => g.key === openGroup.key) ||
            openGroup
          }
          onClose={closeModal}
          onMutate={mutate}
        />
      ) : null}
    </div>
  );
}

// How many memories a card shows before it offers to open in full. Small on
// purpose: the grid is for scanning who Kivi knows about, not for reading
// everything it knows.
const CARD_PREVIEW = 3;

function GroupGrid({ groups, onMutate, kind, onOpen }) {
  if (!groups.length) {
    return (
      <Empty title={`No ${kind} yet`}>
        Kivi learns about {kind} from your dictations. Import a corpus and process it to see
        them here.
      </Empty>
    );
  }
  return (
    <div className="know-grid">
      {groups.map((group) => {
        const hidden = group.memories.length - CARD_PREVIEW;
        return (
          <div className="sheet know-card" key={group.key}>
            <div className="sheet__pad">
              <div className="know-card__head">
                <h3 className="know-card__name">{group.label}</h3>
                <span className="mono tiny muted">{group.memories.length}</span>
              </div>
              {group.subtitle ? <div className="know-card__sub">{group.subtitle}</div> : null}
              {group.memories.slice(0, CARD_PREVIEW).map((memory) => (
                <MemoryRow key={memory.id} memory={memory} onMutate={onMutate} compact />
              ))}
              {hidden > 0 ? (
                <button className="know-card__more" onClick={() => onOpen(group)}>
                  View all {group.memories.length}
                </button>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/**
 * The expanded card: centred, larger, and scrollable, over a dimmed page.
 *
 * Closes on Escape and on a click outside the panel, and locks the page behind
 * it so the grid does not scroll underneath.
 */
// The order a person's memories are worth reading in: what is coming, what you
// owe, who they are, then the history.
const MODAL_SECTIONS = [
  { type: "event", label: "Scheduled" },
  { type: "task", label: "You owe" },
  { type: "fact", label: "About them" },
  { type: "preference", label: "How you like things" },
  { type: "episode", label: "Discussed" },
];

/**
 * The expanded card: centred, larger, scrollable, over a dimmed page.
 *
 * Grouped by type rather than shown as one flat list. Kenji has 31 memories,
 * and an undifferentiated scroll of 31 sentences is not readable — split into
 * "Scheduled / You owe / About them / Discussed" the same 31 become skimmable,
 * and the per-row type pill stops being needed because the heading says it.
 *
 * Closes on Escape and on a click outside, and locks the page behind it.
 */
function GroupModal({ group, onClose, onMutate }) {
  const closeRef = useRef(null);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    // Move focus into the dialog so Escape and Tab behave for keyboard users.
    closeRef.current?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [onClose]);

  if (!group) return null;

  const sections = MODAL_SECTIONS.map((s) => ({
    ...s,
    items: group.memories.filter((m) => m.type === s.type),
  })).filter((s) => s.items.length);

  // Anything with an unexpected type still has to appear.
  const covered = new Set(MODAL_SECTIONS.map((s) => s.type));
  const others = group.memories.filter((m) => !covered.has(m.type));
  if (others.length) sections.push({ type: "other", label: "Other", items: others });

  const summary = sections.map((s) => `${s.items.length} ${s.label.toLowerCase()}`).join(", ");

  // Rendered into <body> rather than in place. A fixed-position overlay is
  // positioned against the nearest ancestor that establishes a containing
  // block, and the page wrapper animates a transform — which centred the dialog
  // inside the content column instead of the viewport, visibly off to the right.
  return createPortal(
    <div
      className="modal"
      role="dialog"
      aria-modal="true"
      aria-label={group.label}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal__panel">
        <header className="modal__head">
          <div className="modal__heading">
            <h2 className="modal__title">{group.label}</h2>
            {group.subtitle ? <div className="modal__sub">{group.subtitle}</div> : null}
            <div className="modal__summary">{summary}</div>
          </div>
          <button className="modal__close" onClick={onClose} aria-label="Close" ref={closeRef}>
            ✕
          </button>
        </header>

        <div className="modal__body">
          {sections.map((section) => (
            <section className="modal__section" key={section.type}>
              <div className="modal__section-head">
                <span className="modal__section-label">{section.label}</span>
                <span className="modal__section-count mono">{section.items.length}</span>
              </div>
              {section.items.map((memory) => (
                <MemoryRow key={memory.id} memory={memory} onMutate={onMutate} bare />
              ))}
            </section>
          ))}
        </div>
      </div>
    </div>,
    document.body,
  );
}

function MemoryRow({ memory, onMutate, showType = false, compact = false, bare = false }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(memory.content);
  const [busy, setBusy] = useState(false);

  async function save() {
    if (!text.trim() || text.trim() === memory.content) {
      setEditing(false);
      return;
    }
    setBusy(true);
    await onMutate(() =>
      api.correctMemory(memory.id, {
        content: text.trim(),
        reason: "corrected on the What Kivi Knows screen",
      }),
    );
    setBusy(false);
    setEditing(false);
  }

  return (
    <div className={`memory${editing ? " memory--editing" : ""}${compact ? " memory--compact" : ""}`}>
      {editing ? (
        <>
          <textarea
            className="memory__edit"
            value={text}
            onChange={(e) => setText(e.target.value)}
            autoFocus
          />
          <div className="memory__foot" style={{ opacity: 1 }}>
            <button className="btn btn--small" onClick={save} disabled={busy}>
              {busy ? <Spinner /> : null} Save correction
            </button>
            <button
              className="btn btn--quiet btn--small"
              onClick={() => {
                setText(memory.content);
                setEditing(false);
              }}
            >
              Cancel
            </button>
          </div>
        </>
      ) : (
        <>
          <div className="memory__text">{memory.content}</div>
          <div className="memory__foot">
            {showType && !bare ? <TypePill type={memory.type} /> : null}
            {!bare &&
            memory.value &&
            !memory.content.toLowerCase().includes(memory.value.toLowerCase()) ? (
              <Pill>{memory.value}</Pill>
            ) : null}
            <button className="btn btn--quiet btn--small" onClick={() => setEditing(true)}>
              Correct
            </button>
            <button
              className="btn btn--quiet btn--small btn--danger"
              onClick={() =>
                onMutate(() => api.forgetMemory(memory.id, "forgotten by the user"))
              }
            >
              Forget
            </button>
          </div>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ lists */

/** A weekday-and-date heading, or a bucket for memories with no date. */
function dayHeading(iso) {
  if (!iso) return "No date given";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "No date given";
  const today = new Date();
  const days = Math.round((date - new Date(today.toDateString())) / 86400000);
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  if (days === -1) return "Yesterday";
  return date.toLocaleDateString(undefined, {
    weekday: "long",
    day: "numeric",
    month: "short",
  });
}

/**
 * The tabs that are one long list rather than a grid of people: what's coming
 * up, what you owe, and how you like things written.
 *
 * "Coming up" is grouped by day, because 129 undated rows is a list you scroll
 * past rather than read — the same content under day headings is a diary. The
 * other two are short enough to read straight through, so they stay flat.
 */
function FlatList({ memories, onMutate, emptyTitle, emptyBody, byDay = false }) {
  if (!memories.length) {
    return <Empty title={emptyTitle}>{emptyBody}</Empty>;
  }

  if (!byDay) {
    return (
      <div className="sheet">
        <div className="sheet__pad">
          {memories.map((memory) => (
            <MemoryRow key={memory.id} memory={memory} onMutate={onMutate} />
          ))}
        </div>
      </div>
    );
  }

  // Undated memories sort last rather than first — an empty string would sort
  // to the top and put "No date given" above everything that actually has one.
  const sorted = [...memories].sort((a, b) =>
    (a.occurred_at || "9999").localeCompare(b.occurred_at || "9999"),
  );

  const days = [];
  for (const memory of sorted) {
    const label = dayHeading(memory.occurred_at);
    const last = days[days.length - 1];
    if (last && last.label === label) last.items.push(memory);
    else days.push({ label, items: [memory] });
  }

  return (
    <div className="daylist">
      {days.map((day) => (
        <section className="sheet" key={day.label}>
          <div className="sheet__pad">
            <div className="daylist__head">
              <span className="daylist__label">{day.label}</span>
              <span className="daylist__count mono">{day.items.length}</span>
            </div>
            {day.items.map((memory) => (
              <MemoryRow key={memory.id} memory={memory} onMutate={onMutate} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

/**
 * Everything Kivi no longer answers from — replaced, forgotten, or never
 * trusted. It is on screen rather than dropped, because "what did Kivi used to
 * believe, and why did it stop?" is the question this whole system has to be
 * able to answer. Forgetting is reversible; a replacement names its successor.
 */
function ArchiveList({ memories, onMutate }) {
  if (!memories.length) {
    return (
      <Empty title="Nothing retired yet">
        When a memory is replaced by a newer one, corrected, or forgotten, it moves here
        instead of being deleted.
      </Empty>
    );
  }

  return (
    <div className="sheet">
      <div className="sheet__pad">
        {memories.map((memory) => (
          <div className="memory memory--archived" key={memory.id}>
            <div className="memory__text">{memory.content}</div>
            <div className="memory__foot" style={{ opacity: 1 }}>
              <StatusPill status={memory.status} />
              <TypePill type={memory.type} />
              {memory.superseded_by_id ? (
                <span className="mono tiny muted">replaced by #{memory.superseded_by_id}</span>
              ) : null}
              <span className="mono tiny muted">{formatStamp(memory.created_at)}</span>
              {memory.status === "DELETED" || memory.status === "REJECTED" ? (
                <button
                  className="btn btn--quiet btn--small"
                  onClick={() => onMutate(() => api.restoreMemory(memory.id))}
                >
                  Restore
                </button>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
