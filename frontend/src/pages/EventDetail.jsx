import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { daySpan, errorText, formatDayShort, formatEventWhen } from "../format.js";
import Attendees from "./Attendees.jsx";
import EventForm from "./EventForm.jsx";
import Register from "./Register.jsx";
import { StatusChip, goToEvent } from "./eventUi.jsx";

const CONFIRMS = {
  cancelled: {
    ask: "Cancel this event? It can't be reopened afterwards, and only its notes can be edited.",
    yes: "Yes, cancel event",
  },
  completed: {
    ask: "Mark this event as completed? Afterwards only its notes can be edited.",
    yes: "Yes, mark completed",
  },
  delete: {
    ask: "Delete this draft for good? This can't be undone.",
    yes: "Yes, delete",
  },
};

export default function EventDetail({ id, role }) {
  const canEdit = role === "secretary" || role === "assistant";
  const headRef = useRef(null);
  const [event, setEvent] = useState(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [confirm, setConfirm] = useState(null); // "cancelled" | "completed" | "delete" | "split" | null
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");

  const load = useCallback(() => {
    return api(`/events/${encodeURIComponent(id)}`)
      .then((ev) => {
        setEvent(ev);
        setError("");
      })
      .catch((e) => setError(errorText(e)));
  }, [id]);
  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (event) headRef.current?.focus();
    // only when the event first appears
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [event === null]);

  const run = async (fn) => {
    setBusy(true);
    setActionError("");
    try {
      await fn();
    } catch (e) {
      setActionError(errorText(e));
    } finally {
      setBusy(false);
      setConfirm(null);
    }
  };

  const setStatus = (status) =>
    run(async () => {
      await api(`/events/${event.id}`, { method: "PATCH", body: { status } });
      await load();
    });
  const duplicate = () =>
    run(async () => {
      const copy = await api(`/events/${event.id}/duplicate`, { method: "POST" });
      goToEvent(copy.id);
    });
  const remove = () =>
    run(async () => {
      await api(`/events/${event.id}`, { method: "DELETE" });
      goToEvent(null);
    });

  const split = () =>
    run(async () => {
      const r = await api(`/events/${event.id}/split-days`, { method: "POST" });
      const first = r.events?.[0]?.id ?? event.id;
      // Day 1 is this same event, so the address doesn't change: reload it to show the days.
      if (String(first) === String(event.id)) await load();
      else goToEvent(first);
    });

  const back = (
    <p>
      <a href="#events">← All events</a>
    </p>
  );

  if (!event) {
    return (
      <>
        {back}
        {error ? (
          <p role="alert" className="error">
            {error}
          </p>
        ) : (
          <p className="muted">Loading…</p>
        )}
      </>
    );
  }

  const open = event.status === "draft" || event.status === "scheduled";
  const series = event.series ?? [];
  const inSeries = series.length >= 2;
  const dayNumber = series.findIndex((d) => String(d.id) === String(event.id)) + 1;
  const span = !inSeries ? daySpan(event.starts_at, event.ends_at) : null;
  const finalised = Boolean(event.finalized_at);
  const attendanceStarted = finalised || (event.attendees ?? []).some((a) => a.status !== "not_recorded");
  const splittable = Boolean(span) && open && !attendanceStarted;
  const confirmInfo =
    confirm === "split"
      ? { ask: `This splits the event into ${span?.days} daily events, each with its own register and the same expected list. Continue?`, yes: "Yes, split into days" }
      : confirm && CONFIRMS[confirm];

  return (
    <>
      {back}
      <div className="item-head">
        <h1 tabIndex={-1} ref={headRef} style={{ margin: 0 }}>
          {event.name}
        </h1>
        <StatusChip status={event.status} />
      </div>

      {inSeries && (
        <section className="days" aria-label="Days of this event">
          <p className="muted">
            Day {dayNumber} of {series.length}
          </p>
          <ul>
            {series.map((d, i) => (
              <li key={d.id}>
                <a href={`#events?id=${d.id}`} aria-current={String(d.id) === String(event.id) ? "page" : undefined}>
                  Day {i + 1} · {formatDayShort(d.starts_at)}
                </a>
                <StatusChip status={d.status} />
                {d.finalized && <span className="chip ok">Finalised</span>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {editing ? (
        <EventForm
          event={event}
          onCancel={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            load();
          }}
        />
      ) : (
        <section className="card" aria-label="Event details">
          <dl className="facts">
            <dt>When</dt>
            <dd>{formatEventWhen(event.starts_at, event.ends_at)}</dd>
            <dt>Where</dt>
            <dd>{event.location || <span className="muted">Not set</span>}</dd>
            <dt>Category</dt>
            <dd>{event.category || <span className="muted">Not set</span>}</dd>
            <dt>Notes</dt>
            <dd className="text">{event.notes || <span className="muted">None</span>}</dd>
          </dl>
          {canEdit && (
            <div className="row-actions">
              {confirmInfo ? (
                <span className="confirm">
                  <button
                    type="button"
                    className={confirm === "split" ? "primary" : "danger"}
                    disabled={busy}
                    onClick={() => (confirm === "delete" ? remove() : confirm === "split" ? split() : setStatus(confirm))}
                  >
                    {confirmInfo.yes}
                  </button>
                  <button type="button" onClick={() => setConfirm(null)}>
                    Keep as it is
                  </button>
                </span>
              ) : (
                <>
                  {event.status === "draft" && (
                    <button type="button" className="primary" disabled={busy} onClick={() => setStatus("scheduled")}>
                      Schedule
                    </button>
                  )}
                  {event.status === "scheduled" && (
                    <button type="button" className="primary" disabled={busy} onClick={() => setConfirm("completed")}>
                      Mark completed
                    </button>
                  )}
                  {open && (
                    <button type="button" disabled={busy} onClick={() => setConfirm("cancelled")}>
                      Cancel event
                    </button>
                  )}
                  {splittable && (
                    <button type="button" disabled={busy} onClick={() => setConfirm("split")}>
                      Take attendance each day
                    </button>
                  )}
                  <button type="button" disabled={busy} onClick={duplicate}>
                    Duplicate
                  </button>
                  <button type="button" disabled={busy} onClick={() => setEditing(true)}>
                    {open ? "Edit" : "Edit notes"}
                  </button>
                  {event.status === "draft" && role === "secretary" && (
                    <button type="button" disabled={busy} onClick={() => setConfirm("delete")}>
                      Delete
                    </button>
                  )}
                </>
              )}
            </div>
          )}
          {confirmInfo && (
            <p role="status" className="muted">
              {confirmInfo.ask}
            </p>
          )}
          {canEdit && span && open && attendanceStarted && !confirmInfo && (
            <p className="muted">Attendance has started, so this event can't be split into days.</p>
          )}
          {actionError && (
            <p role="alert" className="error">
              {actionError}
            </p>
          )}
        </section>
      )}

      <Attendees event={event} canEdit={canEdit && open} inSeries={inSeries} onChanged={load} />

      {(event.status === "scheduled" || event.status === "completed") && <Register event={event} role={role} onChanged={load} />}
      {event.status === "draft" && <p className="muted">Schedule the event to take attendance.</p>}
    </>
  );
}
