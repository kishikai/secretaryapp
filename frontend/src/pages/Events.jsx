import { useEffect, useId, useState } from "react";
import { isAbort, query, swr } from "../api.js";
import { STATUS_LABELS, daysAgoLocal, errorText, formatEventWhen, plural, todayLocal } from "../format.js";
import Sheet from "../Sheet.jsx";
import { SkeletonRows, useIsPhone } from "../ui.jsx";
import EventDetail from "./EventDetail.jsx";
import EventForm from "./EventForm.jsx";
import { StatusChip, goToEvent } from "./eventUi.jsx";

const SCOPES = [
  { key: "upcoming", text: "Upcoming" },
  { key: "past", text: "Past" },
  { key: "all", text: "All" },
];

function EventList({ role }) {
  const id = useId();
  const canEdit = role === "secretary" || role === "assistant";
  const [creating, setCreating] = useState(false);
  const [scope, setScope] = useState("upcoming");
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [showFilters, setShowFilters] = useState(false);
  const [status, setStatus] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [data, setData] = useState(null); // { path, list }: the last answer, kept while the next one loads
  const [error, setError] = useState("");
  const phone = useIsPhone();
  const [sheetOpen, setSheetOpen] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q.trim()), 300);
    return () => clearTimeout(t);
  }, [q]);

  const today = todayLocal();
  const yesterday = daysAgoLocal(1);
  // The Upcoming / Past tabs imply a bound; the From / To fields can only narrow it further.
  let from = dateFrom;
  let to = dateTo;
  if (scope === "upcoming" && (!dateFrom || dateFrom < today)) from = today;
  if (scope === "past" && (!dateTo || dateTo > yesterday)) to = yesterday;

  // Must match the path the shell prefetches for the default (Upcoming) list.
  const path = `/events${query({ q: debouncedQ, status, date_from: from, date_to: to })}`;
  useEffect(() => {
    const ctl = new AbortController();
    setError("");
    swr(
      path,
      (list) => {
        setData({ path, list });
        setError("");
      },
      { signal: ctl.signal },
    ).catch((e) => !isAbort(e) && setError(errorText(e)));
    return () => ctl.abort();
  }, [path]);

  // After a failed load, an answer for other filters would be misleading, so it is hidden.
  const events = data && (data.path === path || !error) ? data.list : null;
  const loading = Boolean(events) && data.path !== path;

  const filtersSet = Boolean(status || dateFrom || dateTo);
  const activeFilters = [status, dateFrom, dateTo].filter(Boolean).length;
  const clearFilters = () => {
    setStatus("");
    setDateFrom("");
    setDateTo("");
  };
  const filterFields = (
    <>
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
      {filtersSet && !phone && (
        <div className="field">
          <button type="button" className="text-button clear-filters" onClick={clearFilters}>
            Clear filters
          </button>
        </div>
      )}
    </>
  );
  const shownEvents = events && scope === "past" ? [...events].reverse() : events;
  const emptyText =
    filtersSet || debouncedQ
      ? `No events match. Change the search or filters${canEdit ? ", or add an event" : ""}.`
      : scope === "upcoming"
        ? "No upcoming events."
        : scope === "past"
          ? "No past events."
          : "No events yet.";

  return (
    <>
      <div className="page-head">
        <h1>Events</h1>
        {canEdit && !creating && (
          <div className="actions">
            <button type="button" className="primary" onClick={() => setCreating(true)}>
              New event
            </button>
          </div>
        )}
      </div>
      {creating && <EventForm onCancel={() => setCreating(false)} onSaved={(created) => goToEvent(created.id)} />}

      <div className="segmented scope-tabs" role="group" aria-label="Which events">
        {SCOPES.map((s) => (
          <button key={s.key} type="button" aria-pressed={scope === s.key} onClick={() => setScope(s.key)}>
            {s.text}
          </button>
        ))}
      </div>

      <div className="searchbar">
        <input type="search" aria-label="Search events" enterKeyHint="search" autoComplete="off" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, place or category" />
        <button
          type="button"
          className="filters-toggle"
          {...(phone
            ? { "aria-haspopup": "dialog", onClick: () => setSheetOpen(true) }
            : { "aria-expanded": showFilters, "aria-controls": `${id}-filters`, onClick: () => setShowFilters((v) => !v) })}
        >
          {activeFilters ? `Filters (${activeFilters})` : "Filters"}
        </button>
        {events && (
          <span className="count" role="status">
            {plural(events.length, "event", "events")}
          </span>
        )}
      </div>
      {showFilters && !phone && (
        <div className="card filters filters-panel" id={`${id}-filters`}>
          {filterFields}
        </div>
      )}
      {phone && (
        <Sheet
          open={sheetOpen}
          onClose={() => setSheetOpen(false)}
          title="Filters"
          className="filters-sheet"
          footer={
            <>
              <button type="button" className="primary grow" onClick={() => setSheetOpen(false)}>
                {events ? `Show ${plural(events.length, "event", "events")}` : "Show events"}
              </button>
              {filtersSet && (
                <button type="button" onClick={clearFilters}>
                  Clear filters
                </button>
              )}
            </>
          }
        >
          <div className="filters-sheet-fields">{filterFields}</div>
        </Sheet>
      )}

      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {!events && !error && <SkeletonRows rows={6} label="Loading events" />}
      {events && events.length === 0 && (
        <p className="empty">
          {emptyText}
          {scope === "upcoming" && !filtersSet && !debouncedQ && canEdit && " Use New event to add one."}
        </p>
      )}
      {shownEvents && shownEvents.length > 0 && (
        <ul className={`rows event-rows${loading ? " is-loading" : ""}`} aria-busy={loading || undefined}>
          {shownEvents.map((ev) => {
            const day = String(ev.starts_at ?? "").slice(0, 10);
            const isToday = day === today;
            const takeRegister = ev.status === "scheduled" && day && day <= today;
            const where = [formatEventWhen(ev.starts_at, ev.ends_at), ev.location].filter(Boolean);
            return (
              <li key={ev.id} className="row row-link">
                <div className="row-main">
                  <div className="row-title">
                    <a href={`#events?id=${ev.id}`}>{ev.name}</a>
                  </div>
                  <div className="row-sub">
                    {where.map((t, i) => (
                      <span key={i}>{t}</span>
                    ))}
                  </div>
                </div>
                <div className="row-side">
                  {isToday && <span className="chip accent">Today</span>}
                  {ev.category && <span className="chip">{ev.category}</span>}
                  <span className="muted expected">{ev.expected_count ?? 0} expected</span>
                  <StatusChip status={ev.status} />
                  {takeRegister && (
                    <a className="button small" href={`#events?id=${ev.id}`}>
                      Take register<span className="sr-only"> for {ev.name}</span>
                    </a>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {events && events.length > 0 && (
        <p>
          <a className="button small" href={`/exports/events.csv${query({ q: debouncedQ, status, date_from: from, date_to: to })}`} download>
            Download these events (CSV)
          </a>
        </p>
      )}
    </>
  );
}

export default function Events({ role, idParam }) {
  if (idParam) return <EventDetail key={idParam} id={idParam} role={role} />;
  return <EventList role={role} />;
}
