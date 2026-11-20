import { useEffect, useState } from "react";
import { api } from "../api.js";
import { errorText, formatDay, formatEventWhen, formatRate, plural } from "../format.js";
import CollectionStatus from "./CollectionStatus.jsx";

function Tile({ label, value }) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value num">{value}</div>
    </div>
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
              Counts only finalised registers.
            </p>
            <div className="meta">
              <span className="num">Present {abs.present}</span>
              <span className="num">Late {abs.late}</span>
              <span className="num">Absent {abs.absent}</span>
              <span className="num">Excused {abs.excused}</span>
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

          {role === "secretary" && (
            <div className="section-gap">
              <CollectionStatus />
            </div>
          )}
        </>
      )}
      {role === "secretary" && !data && error && (
        <div className="section-gap">
          <CollectionStatus />
        </div>
      )}
    </>
  );
}
