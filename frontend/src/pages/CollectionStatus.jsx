import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { formatDay, formatTime, plural } from "../format.js";

function niceStep(raw) {
  const pow = 10 ** Math.floor(Math.log10(raw));
  for (const m of [1, 2, 5, 10]) if (raw <= m * pow) return m * pow;
  return 10 * pow;
}

function DayChart({ data }) {
  const wrapRef = useRef(null);
  const [width, setWidth] = useState(640);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    setWidth(el.clientWidth || 640);
    const ro = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width) || 640));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const height = 220;
  const m = { top: 12, right: 8, bottom: 28, left: 40 };
  const plotW = Math.max(width - m.left - m.right, 10);
  const plotH = height - m.top - m.bottom;
  const counts = data.map((d) => d.count);
  const peak = Math.max(0, ...counts);
  const step = niceStep(Math.max(peak, 4) / 4);
  const yMax = step * 4;
  const band = plotW / Math.max(data.length, 1);
  const barW = band * 0.7;
  const labelEvery = Math.max(1, Math.ceil(46 / band)); // thin labels when narrow
  const y = (v) => m.top + plotH - (v / yMax) * plotH;

  const total = counts.reduce((a, b) => a + b, 0);
  const busiest = data.find((d) => d.count === peak);
  const summary = data.length
    ? `Bar chart of messages per day, ${formatDay(data[0].day)} to ${formatDay(data[data.length - 1].day)}. ` +
      `${plural(total, "message", "messages")} in total.` +
      (peak > 0 ? ` Busiest day ${formatDay(busiest.day)} with ${peak}.` : "")
    : "No data";

  return (
    <div ref={wrapRef} className="chart">
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={summary}>
        {[0, 1, 2, 3, 4].map((i) => (
          <g key={i}>
            <line className="grid" x1={m.left} x2={width - m.right} y1={y(i * step)} y2={y(i * step)} />
            <text className="axis" x={m.left - 6} y={y(i * step)} textAnchor="end" dominantBaseline="middle">
              {i * step}
            </text>
          </g>
        ))}
        {data.map((d, i) => {
          const x = m.left + i * band + (band - barW) / 2;
          const h = plotH - (y(d.count) - m.top);
          return (
            <g key={d.day}>
              <rect className="bar" x={x} y={y(d.count)} width={barW} height={Math.max(h, 0)} rx="2">
                <title>{`${formatDay(d.day)}: ${plural(d.count, "message", "messages")}`}</title>
              </rect>
              {(data.length - 1 - i) % labelEvery === 0 && (
                <text className="axis" x={x + barW / 2} y={height - 8} textAnchor="middle">
                  {formatDay(d.day)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export default function CollectionStatus() {
  const [stats, setStats] = useState(null);
  const [health, setHealth] = useState("checking");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    api("/health")
      .then((h) => !cancelled && setHealth(h.status === "ok" ? "ok" : "down"))
      .catch(() => !cancelled && setHealth("down"));
    api("/stats")
      .then((s) => !cancelled && setStats(s))
      .catch((e) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, []);

  const latest = stats?.latest_message_at ? formatTime(stats.latest_message_at) : null;
  const cats = stats?.by_category ?? [];
  const maxCat = Math.max(1, ...cats.map((c) => c.count));

  return (
    <>
      <h2>Message collection</h2>
      <p className="status-line" role="status">
        <span className={`dot ${health}`} aria-hidden="true" />
        {health === "checking" && "Checking backend…"}
        {health === "ok" && "Backend OK"}
        {health === "down" && "Backend unreachable"}
        {stats && <span className="muted"> · {latest ? `Latest message ${latest}` : "No messages yet"}</span>}
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {!stats && !error && <p className="muted">Loading…</p>}
      {stats && (
        <>
          <div className="tiles">
            <div className="tile">
              <div className="tile-label">Total messages</div>
              <div className="tile-value">{stats.total_messages}</div>
            </div>
            <div className="tile">
              <div className="tile-label">Clipboard vs OCR</div>
              <div className="tile-value">
                {stats.by_source?.clipboard ?? 0} <span className="muted">/</span> {stats.by_source?.ocr ?? 0}
              </div>
            </div>
            <div className="tile">
              <div className="tile-label">Unread notifications</div>
              <div className="tile-value">
                <a href="#notifications">{stats.unread_notifications}</a>
              </div>
            </div>
            <div className="tile">
              <div className="tile-label">Enabled rules</div>
              <div className="tile-value">
                <a href="#rules">{stats.enabled_rules}</a>
              </div>
            </div>
          </div>

          <section className="card">
            <h3>Messages per day (last 14 days)</h3>
            <DayChart data={stats.per_day ?? []} />
          </section>

          <section className="card">
            <h3>Categories</h3>
            {cats.length === 0 ? (
              <p className="muted">No categories yet. Create a rule with a category to tag messages.</p>
            ) : (
              <ul className="cat-list">
                {cats.map((c) => (
                  <li key={c.category}>
                    <span className="cat-name">{c.category}</span>
                    <span className="cat-bar" aria-hidden="true">
                      <span style={{ width: `${(c.count / maxCat) * 100}%` }} />
                    </span>
                    <span className="cat-count">{c.count}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </>
  );
}
