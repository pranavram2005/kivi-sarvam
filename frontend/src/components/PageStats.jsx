/**
 * The analytics strip that sits on top of a screen.
 *
 * Analytics live on the screen they describe rather than in one dashboard off
 * to the side: numbers about your dictations belong above your dictations, and
 * numbers about memory belong above the memory. Each strip collapses, because
 * on most visits you came to read the content, not the statistics.
 */

import { useEffect, useState } from "react";
import { Spinner } from "./ui";

/**
 * `always` renders the figures without a toggle — for screens where the numbers
 * *are* the point (the Inspector) or where they frame what follows (the memory
 * screen). History keeps the toggle: there you usually came to read the feed.
 */
export function StatsStrip({ title, load, render, defaultOpen = false, always = false }) {
  const [open, setOpen] = useState(defaultOpen || always);
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    load()
      .then((d) => alive && setData(d))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (failed) return null;

  if (always) {
    return (
      <section className="strip strip--open strip--static">
        <div className="strip__heading">
          {title}
          {!data ? <Spinner /> : null}
        </div>
        {data ? <div className="strip__body">{render(data)}</div> : null}
      </section>
    );
  }

  return (
    <section className={`strip${open ? " strip--open" : ""}`}>
      <button className="strip__toggle" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="strip__chevron" aria-hidden="true">
          {open ? "▾" : "▸"}
        </span>
        {title}
        {!data ? <Spinner /> : null}
      </button>
      {open && data ? <div className="strip__body">{render(data)}</div> : null}
    </section>
  );
}

/** A number with a label under it — the unit the strips are built from. */
export function Figure({ label, value, note, tone }) {
  return (
    <div className="figure">
      <div className={`figure__value${tone ? ` figure__value--${tone}` : ""}`}>{value}</div>
      <div className="figure__label">{label}</div>
      {note ? <div className="figure__note">{note}</div> : null}
    </div>
  );
}

export function Figures({ children }) {
  return <div className="figures">{children}</div>;
}
