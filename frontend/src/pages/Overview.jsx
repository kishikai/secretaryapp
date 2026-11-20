import { useEffect, useState } from "react";
import { api } from "../api.js";
import { errorText, formatDay, formatEventWhen, formatRate, plural } from "../format.js";
import CollectionStatus from "./CollectionStatus.jsx";
import { MESSAGES_ENABLED } from "../features.js";

function Stat({ label, value, href, lead }) {
  const inner = (
    <>
      <span className="v num">{value}</span>
      <span className="k">{label}</span>
    </>
  );
  return <li className={lead ? "is-lead" : undefined}>{href ? <a className="stat-link" href={href}>{inner}</a> : inner}</li>;
}

const DISMISS_KEY = "secretary.gettingStartedDismissed";

function readDismissed() {
  try {
    return localStorage.getItem(DISMISS_KEY) === "1";
  } catch {
    return false;
  }
}

// Shown until the basics are set up. Hidden when everything is done, still loading, or dismissed.
function GettingStarted({ data, role }) {
  const [dismissed, setDismissed] = useState(readDismissed);
  const [events, setEvents] = useState(null); // { any, completed }

  useEffect(() => {
    if (dismissed) return undefined;
    let cancelled = false;
    Promise.all([api("/events"), api("/events?status=completed")])
      .then(([all, done]) => !cancelled && setEvents({ any: all.length > 0, completed: done.length > 0 }))
      .catch(() => {}); // no card if we can't tell
    return () => {
      cancelled = true;
    };
  }, [dismissed]);

  if (dismissed || !events) return null;

  const steps = [
    {
      done: data.members.active + data.members.inactive > 0,
      href: "#members",
      text: "Add your members",
      hint: role === "secretary" ? "Add them one by one, or use Import from spreadsheet." : "",
    },
    { done: data.groups > 0, href: "#groups", text: "Put members into groups", hint: "" },
    { done: events.any, href: "#events", text: "Create your first event", hint: "" },
    { done: events.completed, href: "#events", text: "Take attendance and finalise a register", hint: "Open an event and mark who came." },
  ];
  if (steps.every((st) => st.done)) return null;

  const hide = () => {
    try {
      localStorage.setItem(DISMISS_KEY, "1");
    } catch {
      // still hide it for this visit
    }
    setDismissed(true);
  };

  return (
    <section className="card ov-start" aria-labelledby="ov-start">
      <div className="card-head">
        <h2 id="ov-start">Getting started</h2>
        <button type="button" className="text-button" onClick={hide}>
          Hide this
        </button>
      </div>
      <ul className="checklist">
        {steps.map((st) => (
          <li key={st.text} className={st.done ? "done" : undefined}>
            <span className="tick" aria-hidden="true">
              {st.done ? "✓" : ""}
            </span>
            <span>
              <a href={st.href}>{st.text}</a>
              <span className="sr-only">{st.done ? " (done)" : " (not done yet)"}</span>
              {!st.done && st.hint && <span className="muted"> {st.hint}</span>}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function Overview({ role }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    api("/dashboard")
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(errorText(e)));
    return () => {
      cancelled = true;
    };
  }, []);

  const abs = data?.absences;

  return (
    <>
      <div className="page-head">
        <h1>Overview</h1>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {!data && !error && <p className="muted">Loading…</p>}
      {data && (
        <>
          {role !== "viewer" && <GettingStarted data={data} role={role} />}
          <ul className="stat-strip ov-stats">
            <Stat label="Active members" value={data.members.active} href="#members" />
            <Stat label="Groups" value={data.groups} href="#groups" />
            <Stat label="Upcoming events" value={data.upcoming_count} href="#events" />
            <Stat label="Registers to finish" value={data.registers_to_finish.length} />
          </ul>

          <div className="ov-grid">
            <section className="card" aria-labelledby="ov-finish">
              <h2 id="ov-finish">Needs doing</h2>
              {data.registers_to_finish.length === 0 ? (
                <p className="muted">All registers are finalised.</p>
              ) : (
                <ul className="rows ov-rows">
                  {data.registers_to_finish.map((ev) => (
                    <li key={ev.id} className="row">
                      <div className="row-main">
                        <div className="row-title">
                          <a href={`#events?id=${ev.id}`}>{ev.name}</a>
                        </div>
                        <div className="row-sub">
                          <span>{formatEventWhen(ev.starts_at, ev.ends_at)}</span>
                        </div>
                      </div>
                      <div className="row-side">
                        <span className="num ov-count">{ev.not_recorded} not recorded</span>
                      </div>
                      <div className="row-actions-inline">
                        <a className="button small primary" href={`#events?id=${ev.id}`}>
                          Finish register<span className="sr-only"> for {ev.name}</span>
                        </a>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="card" aria-labelledby="ov-upcoming">
              <h2 id="ov-upcoming">Next up</h2>
              {data.upcoming_events.length === 0 ? (
                <p className="muted">No upcoming events.</p>
              ) : (
                <ul className="rows ov-rows">
                  {data.upcoming_events.map((ev) => (
                    <li key={ev.id} className="row">
                      <div className="row-main">
                        <div className="row-title">
                          <a href={`#events?id=${ev.id}`}>{ev.name}</a>
                        </div>
                        <div className="row-sub">
                          <span>{formatEventWhen(ev.starts_at, ev.ends_at)}</span>
                        </div>
                      </div>
                      <div className="row-side">
                        <span className="num ov-count">{ev.expected_count} expected</span>
                      </div>
                      <div className="row-actions-inline">
                        <a className="button small" href={`#events?id=${ev.id}`}>
                          Open<span className="sr-only"> {ev.name}</span>
                        </a>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              <p className="ov-more">
                <a href="#events">See all events</a>
              </p>
            </section>
          </div>

          <section className="card" aria-labelledby="ov-absences">
            <div className="card-head">
              <h2 id="ov-absences">Absences, last 90 days</h2>
              {abs.date_from && abs.date_to && (
                <span className="muted">
                  {formatDay(abs.date_from)} to {formatDay(abs.date_to)}
                </span>
              )}
            </div>
            <ul className="stat-strip abs-stats">
              <Stat lead label="Absence rate" value={formatRate(abs.absence_rate)} />
              <Stat label="Present" value={abs.present} />
              <Stat label="Late" value={abs.late} />
              <Stat label="Late (excused)" value={abs.late_excused} />
              <Stat label="Absent" value={abs.absent} />
              <Stat label="Absent (excused)" value={abs.absent_excused} />
            </ul>
            <details className="explain">
              <summary>How is this worked out?</summary>
              <p>
                Counts only finalised registers of events that weren't cancelled. Absence rate = absent ÷ (present + late + late excused + absent); absent (excused) isn't counted.
              </p>
            </details>
            <h3>Most absences</h3>
            {data.most_absent.length === 0 ? (
              <p className="muted">No absences recorded in this period.</p>
            ) : (
              <ul className="rows ov-rows">
                {data.most_absent.map((m) => (
                  <li key={m.member_id} className="row">
                    <div className="row-main">
                      <div className="row-title">
                        <a href={`#members?id=${m.member_id}`}>{m.full_name}</a>
                      </div>
                    </div>
                    <div className="row-side">
                      {!m.active && <span className="chip">Inactive</span>}
                      <span className="num ov-count">{plural(m.absent, "absence", "absences")}</span>
                      <span className="num ov-rate">{formatRate(m.absence_rate)}</span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
            <p className="ov-more">
              <a href="#absences">See all absences</a>
            </p>
          </section>

          {role === "secretary" && MESSAGES_ENABLED && (
            <div className="section-gap">
              <CollectionStatus />
            </div>
          )}
        </>
      )}
      {role === "secretary" && MESSAGES_ENABLED && !data && error && (
        <div className="section-gap">
          <CollectionStatus />
        </div>
      )}
    </>
  );
}
