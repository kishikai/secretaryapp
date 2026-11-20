import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { errorText, formatEventWhen } from "../format.js";
import Attendees from "./Attendees.jsx";
import EventForm from "./EventForm.jsx";
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
  const [confirm, setConfirm] = useState(null); // "cancelled" | "completed" | "delete" | null
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
  const confirmInfo = confirm && CONFIRMS[confirm];

  return (
    <>
      {back}
      <div className="item-head">
        <h1 tabIndex={-1} ref={headRef} style={{ margin: 0 }}>
          {event.name}
        </h1>
        <StatusChip status={event.status} />
      </div>

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
                    className="danger"
                    disabled={busy}
                    onClick={() => (confirm === "delete" ? remove() : setStatus(confirm))}
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
          {actionError && (
            <p role="alert" className="error">
              {actionError}
            </p>
          )}
        </section>
      )}

      <Attendees event={event} canEdit={canEdit && open} onChanged={load} />
    </>
  );
}
