import { useEffect, useId, useState } from "react";
import { api, isAbort, query } from "../api.js";
import { STATUS_LABELS, errorText, formatEventWhen, plural, todayLocal } from "../format.js";
import EventDetail from "./EventDetail.jsx";
import EventForm from "./EventForm.jsx";
import { StatusChip, goToEvent } from "./eventUi.jsx";

function EventList({ role }) {
  const id = useId();
  const canEdit = role === "secretary" || role === "assistant";
  const [creating, setCreating] = useState(false);
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [status, setStatus] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [upcoming, setUpcoming] = useState(false);
  const [events, setEvents] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q.trim()), 300);
    return () => clearTimeout(t);
  }, [q]);

  const today = todayLocal();
  const from = upcoming && (!dateFrom || dateFrom < today) ? today : dateFrom;

  useEffect(() => {
    const ctl = new AbortController();
    api(`/events${query({ q: debouncedQ, status, date_from: from, date_to: dateTo })}`, { signal: ctl.signal })
      .then((list) => {
        setEvents(list);
        setError("");
      })
      .catch((e) => !isAbort(e) && setError(errorText(e)));
    return () => ctl.abort();
  }, [debouncedQ, status, from, dateTo]);

  return (
    <>
      <h1>Events</h1>
      <div className="toolbar">
        {canEdit && !creating && (
          <button type="button" className="primary" onClick={() => setCreating(true)}>
            New event
          </button>
        )}
      </div>
      {creating && <EventForm onCancel={() => setCreating(false)} onSaved={(created) => goToEvent(created.id)} />}
      <div className="card filters">
        <div className="field">
          <label htmlFor={`${id}-q`}>Search</label>
          <input id={`${id}-q`} type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, place or category" />
        </div>
        <div className="field">
          <label htmlFor={`${id}-status`}>Status</label>
          <select id={`${id}-status`} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All</option>
            {Object.entries(STATUS_LABELS).map(([v, label]) => (
              <option key={v} value={v}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor={`${id}-from`}>From date</label>
          <input id={`${id}-from`} type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor={`${id}-to`}>To date</label>
          <input id={`${id}-to`} type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
        </div>
        <label className="check">
          <input type="checkbox" checked={upcoming} onChange={(e) => setUpcoming(e.target.checked)} />
          Upcoming only
        </label>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {!events && !error && <p className="muted">Loading…</p>}
      {events && (
        <p className="muted" role="status">
          {plural(events.length, "event", "events")}
        </p>
      )}
      {events && events.length === 0 && <p className="empty">No events match. Change the search or filters{canEdit ? ", or add an event" : ""}.</p>}
      <ul className="list">
        {(events ?? []).map((ev) => (
          <li key={ev.id} className="card item">
            <div className="item-head">
              <h2>
                <a href={`#events?id=${ev.id}`}>{ev.name}</a>
              </h2>
              <StatusChip status={ev.status} />
            </div>
            <div className="meta">
              <span>{formatEventWhen(ev.starts_at, ev.ends_at)}</span>
              {ev.location && <span>{ev.location}</span>}
              {ev.category && <span className="chip accent">{ev.category}</span>}
              <span>{ev.expected_count ?? 0} expected</span>
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

export default function Events({ role, idParam }) {
  if (idParam) return <EventDetail key={idParam} id={idParam} role={role} />;
  return <EventList role={role} />;
}
