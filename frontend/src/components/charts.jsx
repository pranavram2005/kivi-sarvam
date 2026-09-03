/**
 * Chart primitives — inline SVG, no charting library.
 *
 * Colour follows the job, not taste:
 *
 *   categorical (identity)  memory types — five fixed slots, assigned in order,
 *                           never cycled, always with a legend and direct labels
 *   sequential  (magnitude) nominal bars — every bar the SAME hue, because bar
 *                           length already encodes the value; colouring bars by
 *                           their own value spends the identity channel on
 *                           nothing
 *   status      (state)     memory status — reserved colours, never reused as a
 *                           series, always shipped with a written label
 *
 * The categorical slots below are the documented dark-mode palette, validated
 * against this app's chart surface (#151a14): lightness band, chroma floor,
 * CVD separation (worst adjacent ΔE 8.4 protan), normal-vision floor (19.3) and
 * contrast all pass.
 *
 * Mark specs: thin bars, 4px rounded data-ends anchored to the baseline, a 2px
 * surface gap between adjacent fills, recessive axes, and a hover layer on
 * everything that plots.
 */

import { useId, useState } from "react";

export const SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"];

/** One hue for magnitude — the accent, so a bar list reads as "the same thing, more of it". */
export const MAGNITUDE = "#8fbf4d";

export const STATUS_COLOR = {
  ACTIVE: "#a8e063",
  SUPERSEDED: "#d9a05b",
  REJECTED: "#7c7a6e",
  DELETED: "#d98570",
};

const TYPE_LABEL = {
  fact: "Fact",
  event: "Scheduled",
  task: "Commitment",
  preference: "Preference",
  episode: "Discussion",
};
const STATUS_LABEL = {
  ACTIVE: "Current",
  SUPERSEDED: "Replaced",
  REJECTED: "Not trusted",
  DELETED: "Forgotten",
};

export const typeLabel = (k) => TYPE_LABEL[k] || k;
export const statusLabel = (k) => STATUS_LABEL[k] || k;

const fmt = (n) => Number(n ?? 0).toLocaleString();

/* ------------------------------------------------------------------ tiles */
export function StatTile({ label, value, note, tone }) {
  return (
    <div className="stat">
      <div className="stat__label">{label}</div>
      <div className={`stat__value${tone ? ` stat__value--${tone}` : ""}`}>{value}</div>
      {note ? <div className="stat__note">{note}</div> : null}
    </div>
  );
}

/* ------------------------------------------------- nominal magnitude bars */
/**
 * Horizontal bars for nominal categories (apps, people, projects). One hue for
 * every bar: length is the encoding, colour would be re-encoding it.
 */
export function BarList({ rows, valueKey = "count", suffix = "", max, formatNote }) {
  const [hover, setHover] = useState(null);
  if (!rows?.length) return <p className="small muted">No data yet.</p>;
  const top = max ?? Math.max(...rows.map((r) => r[valueKey] || 0), 1);

  return (
    <div className="barlist">
      {rows.map((r) => {
        const v = r[valueKey] || 0;
        const pct = Math.max(1.5, (v / top) * 100);
        return (
          <div
            className="barlist__row"
            key={r.key}
            onMouseEnter={() => setHover(r.key)}
            onMouseLeave={() => setHover(null)}
          >
            <div className="barlist__name" title={r.key}>
              {r.key}
            </div>
            <div className="barlist__track">
              <div
                className="barlist__fill"
                style={{ width: `${pct}%`, background: MAGNITUDE }}
              />
              {hover === r.key && formatNote ? (
                <span className="barlist__tip">{formatNote(r)}</span>
              ) : null}
            </div>
            <div className="barlist__value mono">
              {fmt(v)}
              {suffix}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------ categorical composition */
/**
 * A single stacked bar showing composition, plus a legend with direct values.
 * Used for memory types (categorical) and statuses (status palette).
 */
export function CompositionBar({ rows, colorFor, labelFor, total }) {
  const [hover, setHover] = useState(null);
  if (!rows?.length) return <p className="small muted">No data yet.</p>;
  const sum = (total ?? rows.reduce((a, r) => a + (r.count || 0), 0)) || 1;

  return (
    <div>
      <div className="composition" role="img" aria-label="composition">
        {rows.map((r) => (
          <div
            key={r.key}
            className="composition__seg"
            style={{
              flexGrow: Math.max(r.count, 0.001),
              background: colorFor(r.key),
              opacity: hover && hover !== r.key ? 0.35 : 1,
            }}
            title={`${labelFor(r.key)}: ${fmt(r.count)}`}
            onMouseEnter={() => setHover(r.key)}
            onMouseLeave={() => setHover(null)}
          />
        ))}
      </div>
      <div className="legend">
        {rows.map((r) => (
          <div
            className="legend__item"
            key={r.key}
            onMouseEnter={() => setHover(r.key)}
            onMouseLeave={() => setHover(null)}
          >
            <span className="legend__dot" style={{ background: colorFor(r.key) }} />
            <span className="legend__label">{labelFor(r.key)}</span>
            <span className="legend__value mono">{fmt(r.count)}</span>
            <span className="legend__pct mono">{Math.round((r.count / sum) * 100)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* -------------------------------------------------------- trend over time */
/**
 * A single series over time: one hue, no legend (the title names it), a
 * crosshair and a tooltip on hover.
 */
export function AreaTrend({
  points,
  xKey,
  yKey,
  secondaryKey,
  height = 190,
  label,
  secondaryLabel,
}) {
  // Both series share one axis, always. Two measures of different magnitude
  // (a running total and a weekly count) do not belong on the same chart at
  // all - they get two panels - so anything drawn here is comparable by
  // construction.
  const gradientId = useId();
  const [hover, setHover] = useState(null);
  if (!points?.length) return <p className="small muted">No data yet.</p>;

  const W = 760;
  const H = height;
  const padL = 8;
  const padR = 8;
  const padT = 14;
  const padB = 26;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const ys = points.map((p) => p[yKey] || 0);
  const ys2 = secondaryKey ? points.map((p) => p[secondaryKey] || 0) : [];
  const top = Math.max(...ys, ...ys2, 1);

  const x = (i) => padL + (points.length === 1 ? plotW / 2 : (i / (points.length - 1)) * plotW);
  const y = (v) => padT + plotH - (v / top) * plotH;

  const line = (key) =>
    points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[key] || 0).toFixed(1)}`).join(" ");
  const area = `${line(yKey)} L${x(points.length - 1).toFixed(1)},${padT + plotH} L${x(0).toFixed(1)},${padT + plotH} Z`;

  function onMove(e) {
    const box = e.currentTarget.getBoundingClientRect();
    const rel = ((e.clientX - box.left) / box.width) * W;
    const i = Math.round(((rel - padL) / plotW) * (points.length - 1));
    setHover(Math.max(0, Math.min(points.length - 1, i)));
  }

  const hp = hover !== null ? points[hover] : null;

  return (
    <div className="trend">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="trend__svg"
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        role="img"
        aria-label={label}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={MAGNITUDE} stopOpacity="0.26" />
            <stop offset="100%" stopColor={MAGNITUDE} stopOpacity="0" />
          </linearGradient>
        </defs>

        {/* recessive baseline only - no grid competing with the data */}
        <line
          x1={padL} y1={padT + plotH} x2={W - padR} y2={padT + plotH}
          stroke="rgba(255,255,255,0.09)" strokeWidth="1"
        />

        <path d={area} fill={`url(#${gradientId})`} />
        {secondaryKey ? (
          <path
            d={line(secondaryKey)} fill="none"
            stroke="rgba(255,255,255,0.22)" strokeWidth="2"
            strokeDasharray="3 4" strokeLinecap="round"
          />
        ) : null}
        <path d={line(yKey)} fill="none" stroke={MAGNITUDE} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />

        {hp ? (
          <>
            <line
              x1={x(hover)} y1={padT} x2={x(hover)} y2={padT + plotH}
              stroke="rgba(255,255,255,0.18)" strokeWidth="1"
            />
            <circle cx={x(hover)} cy={y(hp[yKey] || 0)} r="4.5" fill={MAGNITUDE}
                    stroke="#151a14" strokeWidth="2" />
          </>
        ) : null}

        <text x={padL} y={H - 8} className="trend__axis">{points[0][xKey]}</text>
        <text x={W - padR} y={H - 8} textAnchor="end" className="trend__axis">
          {points[points.length - 1][xKey]}
        </text>
      </svg>

      {secondaryKey ? (
        <div className="trend__legend">
          <span className="trend__key">
            <i style={{ background: MAGNITUDE }} /> {label}
          </span>
          <span className="trend__key">
            <i className="trend__key--dashed" /> {secondaryLabel}
          </span>
        </div>
      ) : null}

      <div className="trend__readout mono">
        {hp ? (
          <>
            <span>{hp[xKey]}</span>
            <span style={{ color: MAGNITUDE }}>{fmt(hp[yKey])} {label}</span>
            {secondaryKey ? <span className="muted">{fmt(hp[secondaryKey])} {secondaryLabel}</span> : null}
          </>
        ) : (
          <span className="muted">hover the chart for a week-by-week reading</span>
        )}
      </div>
    </div>
  );
}

/* ----------------------------------------------------------- table view */
/** Every chart on the page is also available as numbers. */
export function DataTable({ columns, rows }) {
  return (
    <div className="table__scroll">
      <table className="table">
        <thead>
          <tr>{columns.map((c) => <th key={c.key}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c.key} className={c.mono ? "mono" : undefined}>
                  {c.render ? c.render(r) : r[c.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
