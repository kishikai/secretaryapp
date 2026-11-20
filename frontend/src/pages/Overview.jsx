import { useEffect, useState } from "react";
import { api } from "../api.js";
import { errorText, formatDay, formatEventWhen, formatRate, plural } from "../format.js";
import CollectionStatus from "./CollectionStatus.jsx";
import { MESSAGES_ENABLED } from "../features.js";

function Tile({ label, value }) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value num">{value}</div>
    </div>
  );
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
    <section className="card" aria-labelledby="ov-start">
      <h2 id="ov-start">Getting started</h2>
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
      <button type="button" onClick={hide}>
        Hide this
      </button>
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
      <h1>Overview</h1>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {!data && !error && <p className="muted">Loading…</p>}
      {data && (
        <>
          {role !== "viewer" && <GettingStarted data={data} role={role} />}
          <div className="tiles">
            <Tile label="Active members" value={data.members.active} />
            <Tile label="Groups" value={data.groups} />
            <Tile label="Upcoming events" value={data.upcoming_count} />
            <Tile label="Registers to finish" value={data.registers_to_finish.length} />
          </div>

          <section className="card" aria-labelledby="ov-finish">
            <h2 id="ov-finish">Registers to finish</h2>
            {data.registers_to_finish.length === 0 ? (
              <p className="muted">All registers are finalised.</p>
            ) : (
              <ul className="rows-list">
                {data.registers_to_finish.map((ev) => (
                  <li key={ev.id}>
                    <span className="grow">
                      <a href={`#events?id=${ev.id}`}>{ev.name}</a>
                      <span className="sub">{formatEventWhen(ev.starts_at, ev.ends_at)}</span>
                    </span>
                    <span className="num">{ev.not_recorded} not recorded</span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="card" aria-labelledby="ov-upcoming">
            <h2 id="ov-upcoming">Upcoming events</h2>
            {data.upcoming_events.length === 0 ? (
              <p className="muted">No upcoming events.</p>
            ) : (
              <ul className="rows-list">
                {data.upcoming_events.map((ev) => (
                  <li key={ev.id}>
                    <span className="grow">
                      <a href={`#events?id=${ev.id}`}>{ev.name}</a>
                      <span className="sub">{formatEventWhen(ev.starts_at, ev.ends_at)}</span>
                    </span>
                    <span className="num">{ev.expected_count} expected</span>
                  </li>
                ))}
              </ul>
            )}
            <p>
              <a href="#events">See all events</a>
            </p>
          </section>

          <section className="card" aria-labelledby="ov-absences">
            <h2 id="ov-absences">Absences in the last 90 days</h2>
            <p className="muted">
              {abs.date_from && abs.date_to ? `${formatDay(abs.date_from)} to ${formatDay(abs.date_to)}. ` : ""}
              Counts only finalised registers of events that weren't cancelled. Absence rate = absent ÷ (present + late + late excused + absent); absent (excused) isn't counted.
            </p>
            <div className="meta">
              <span className="num">Present {abs.present}</span>
              <span className="num">Late {abs.late}</span>
              <span className="num">Late (excused) {abs.late_excused}</span>
              <span className="num">Absent {abs.absent}</span>
              <span className="num">Absent (excused) {abs.absent_excused}</span>
              <span className="num">
                <strong>Absence rate {formatRate(abs.absence_rate)}</strong>
              </span>
            </div>
            <h3>Most absences</h3>
            {data.most_absent.length === 0 ? (
              <p className="muted">No absences recorded in this period.</p>
            ) : (
              <ul className="rows-list">
                {data.most_absent.map((m) => (
                  <li key={m.member_id}>
                    <span className="grow">
                      <a href={`#members?id=${m.member_id}`}>{m.full_name}</a>
                      {!m.active && <span className="chip"> Inactive</span>}
                    </span>
                    <span className="num">{plural(m.absent, "absence", "absences")}</span>
                    <span className="num">{formatRate(m.absence_rate)}</span>
                  </li>
                ))}
              </ul>
            )}
            <p>
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
