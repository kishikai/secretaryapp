import { useCallback, useEffect, useRef, useState } from "react";
import { api, isAbort, swr } from "../api.js";
import { daySpan, errorText, formatDayShort, formatEventWhen } from "../format.js";
import Sheet from "../Sheet.jsx";
import { SkeletonBlock, SkeletonRows, useIsPhone } from "../ui.jsx";
import Attendees from "./Attendees.jsx";
import EventForm from "./EventForm.jsx";
import Register from "./Register.jsx";
import { StatusChip, goToEvent, useIdempotencyKey } from "./eventUi.jsx";

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
  const menuRef = useRef(null);
  const keepRef = useRef(null);
  const phone = useIsPhone();
  const [moreOpen, setMoreOpen] = useState(false);
  const duplicateKey = useIdempotencyKey();
  const splitKey = useIdempotencyKey();

  // The path must match the one the shell prefetches. A cached copy shows at once and is replaced by the fresh one;
  // if the fresh load fails the cached copy stays and the error shows above it.
  const load = useCallback(
    (signal) =>
      swr(
        `/events/${encodeURIComponent(id)}`,
        (ev) => {
          setEvent(ev);
          setError("");
        },
        { signal },
      ).catch((e) => {
        if (!isAbort(e)) setError(errorText(e));
      }),
    [id],
  );
  useEffect(() => {
    const ctl = new AbortController();
    load(ctl.signal);
    return () => ctl.abort();
  }, [load]);
  const reload = useCallback(() => load(), [load]);

  useEffect(() => {
    if (event) headRef.current?.focus();
    // only when the event first appears
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [event === null]);

  // The "More" menu closes when an item is chosen, on Escape, and on a click elsewhere.
  const closeMenu = (refocus) => {
    const m = menuRef.current;
    if (m?.open) {
      m.open = false;
      if (refocus) m.querySelector("summary")?.focus();
    }
  };
  useEffect(() => {
    const onDown = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) closeMenu(false);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, []);
  useEffect(() => {
    if (confirm) keepRef.current?.focus();
  }, [confirm]);

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
      await reload();
    });
  const duplicate = () =>
    run(async () => {
      const copy = await api(`/events/${event.id}/duplicate`, { method: "POST", idempotencyKey: duplicateKey.get(String(event.id)) });
      duplicateKey.renew();
      goToEvent(copy.id);
    });
  const remove = () =>
    run(async () => {
      await api(`/events/${event.id}`, { method: "DELETE" });
      goToEvent(null);
    });

  const split = () =>
    run(async () => {
      const r = await api(`/events/${event.id}/split-days`, { method: "POST", idempotencyKey: splitKey.get(String(event.id)) });
      splitKey.renew();
      const first = r.events?.[0]?.id ?? event.id;
      // Day 1 is this same event, so the address doesn't change: reload it to show the days.
      if (String(first) === String(event.id)) await reload();
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
          <>
            <SkeletonBlock lines={3} label="Loading event" />
            <SkeletonRows rows={5} label="Loading the register" />
          </>
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

  const when = [formatEventWhen(event.starts_at, event.ends_at), event.location, event.category].filter(Boolean);
  const pick = (fn) => () => {
    closeMenu(true);
    fn();
  };
  // In the bottom sheet: close it first; a destructive choice then shows its inline confirmation.
  const pickInSheet = (fn) => () => {
    setMoreOpen(false);
    fn();
  };
  const moreItems = (choose) => (
    <>
      <button type="button" disabled={busy} onClick={choose(duplicate)}>
        Duplicate
      </button>
      {splittable && (
        <button type="button" disabled={busy} onClick={choose(() => setConfirm("split"))}>
          Take attendance each day
        </button>
      )}
      {open && (
        <button type="button" disabled={busy} onClick={choose(() => setConfirm("cancelled"))}>
          Cancel event
        </button>
      )}
      {event.status === "draft" && role === "secretary" && (
        <button type="button" className="danger" disabled={busy} onClick={choose(() => setConfirm("delete"))}>
          Delete
        </button>
      )}
    </>
  );

  return (
    <>
      <p className="back-link">
        <a href="#events">← All events</a>
      </p>
      <div className="page-head detail-head">
        <h1 tabIndex={-1} ref={headRef}>
          {event.name}
        </h1>
        <StatusChip status={event.status} />
        {canEdit && !editing && (
          <div className="actions">
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
            <button type="button" disabled={busy} onClick={() => setEditing(true)}>
              {open ? "Edit" : "Edit notes"}
            </button>
            {phone ? (
              <>
                <button type="button" className="menu-btn" aria-haspopup="dialog" onClick={() => setMoreOpen(true)}>
                  More
                </button>
                <Sheet open={moreOpen} onClose={() => setMoreOpen(false)} title="More actions">
                  <div className="sheet-menu">{moreItems(pickInSheet)}</div>
                </Sheet>
              </>
            ) : (
              <details
                className="menu"
                ref={menuRef}
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    e.stopPropagation();
                    closeMenu(true);
                  }
                }}
              >
                <summary>More</summary>
                <div className="menu-panel">{moreItems(pick)}</div>
              </details>
            )}
          </div>
        )}
      </div>

      {confirmInfo && !editing && (
        <div className="notice confirm-box">
          <p role="status">{confirmInfo.ask}</p>
          <span className="confirm">
            <button
              type="button"
              className={confirm === "split" ? "primary" : "danger"}
              disabled={busy}
              onClick={() => (confirm === "delete" ? remove() : confirm === "split" ? split() : setStatus(confirm))}
            >
              {confirmInfo.yes}
            </button>
            <button type="button" ref={keepRef} onClick={() => setConfirm(null)}>
              Keep as it is
            </button>
          </span>
        </div>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {actionError && (
        <p role="alert" className="error">
          {actionError}
        </p>
      )}

      {editing ? (
        <EventForm
          event={event}
          onCancel={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            reload();
          }}
        />
      ) : (
        <div className="event-facts">
          {when.length > 0 && (
            <p className="muted facts-line">
              {when.map((t, i) => (
                <span key={i}>{t}</span>
              ))}
            </p>
          )}
          {event.notes && <p className="text event-notes">{event.notes}</p>}
          {canEdit && span && open && attendanceStarted && !confirmInfo && (
            <p className="muted">Attendance has started, so this event can&rsquo;t be split into days.</p>
          )}
        </div>
      )}

      {inSeries && (
        <section className="days-strip" aria-label={`Days of this event (day ${dayNumber} of ${series.length})`}>
          <ul>
            {series.map((d, i) => (
              <li key={d.id}>
                <a href={`#events?id=${d.id}`} aria-current={String(d.id) === String(event.id) ? "page" : undefined}>
                  Day {i + 1} · {formatDayShort(d.starts_at)}
                </a>
                {d.status !== "scheduled" && <StatusChip status={d.status} />}
                {d.finalized && <span className="chip ok">Finalised</span>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {(event.status === "scheduled" || event.status === "completed") && <Register event={event} role={role} onChanged={reload} />}
      <Attendees event={event} canEdit={canEdit && open} inSeries={inSeries} defaultOpen={event.status === "draft" || (event.attendees ?? []).length === 0} onChanged={reload} />
      {event.status === "draft" && <p className="muted">Schedule the event to take attendance.</p>}
    </>
  );
}
