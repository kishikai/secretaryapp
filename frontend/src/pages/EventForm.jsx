import { useEffect, useId, useRef, useState } from "react";
import { api } from "../api.js";
import { MAX_SPLIT_DAYS, daySpan, errorText, toLocalInput } from "../format.js";

// Create (event = null) or edit. A completed or cancelled event only lets the notes change.
export default function EventForm({ event, onSaved, onCancel }) {
  const id = useId();
  const headRef = useRef(null);
  const [f, setF] = useState({
    name: event?.name ?? "",
    starts_at: toLocalInput(event?.starts_at),
    ends_at: toLocalInput(event?.ends_at),
    location: event?.location ?? "",
    category: event?.category ?? "",
    notes: event?.notes ?? "",
  });
  const [status, setStatus] = useState("draft");
  const [categories, setCategories] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [split, setSplit] = useState(true);
  const [createdId, setCreatedId] = useState(null); // set when the event was created but splitting it failed
  const span = event ? null : daySpan(f.starts_at, f.ends_at);
  const canSplit = Boolean(span && span.timesOk && !span.tooLong);
  const locked = event && (event.status === "completed" || event.status === "cancelled");

  useEffect(() => {
    headRef.current?.focus();
    api("/categories")
      .then((c) => Array.isArray(c) && setCategories(c))
      .catch(() => {});
  }, []);

  const set = (k) => (e) => setF((cur) => ({ ...cur, [k]: e.target.value }));

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    if (!locked) {
      if (!f.name.trim()) return setError("Give the event a name");
      if (f.starts_at && f.ends_at && f.ends_at < f.starts_at) return setError("The end time must be after the start time");
    }
    const body = locked
      ? { notes: f.notes.trim() || null }
      : {
          name: f.name.trim(),
          starts_at: f.starts_at || null,
          ends_at: f.ends_at || null,
          location: f.location.trim() || null,
          category: f.category.trim() || null,
          notes: f.notes.trim() || null,
        };
    setBusy(true);
    try {
      if (event) {
        await api(`/events/${event.id}`, { method: "PATCH", body });
        onSaved();
      } else {
        const created = await api("/events", { method: "POST", body: { ...body, status } });
        if (canSplit && split) {
          try {
            const r = await api(`/events/${created.id}/split-days`, { method: "POST" });
            onSaved(r.events?.[0] ?? created);
          } catch (err) {
            setCreatedId(created.id);
            setError(`The event was saved, but it could not be split into days: ${errorText(err)}`);
          }
        } else {
          onSaved(created);
        }
      }
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const field = (k, label, props = {}) => (
    <div className="field">
      <label htmlFor={`${id}-${k}`}>{label}</label>
      <input id={`${id}-${k}`} value={f[k]} onChange={set(k)} disabled={locked} {...props} />
    </div>
  );

  return (
    <form className="card" onSubmit={submit}>
      <h2 tabIndex={-1} ref={headRef}>
        {event ? (locked ? `Edit notes for ${event.name}` : `Edit ${event.name}`) : "New event"}
      </h2>
      {locked && (
        <p className="muted">
          This event is {event.status}, so only the notes can be changed.
        </p>
      )}
      <div className="form-grid">
        {field("name", "Name (required)", { required: !locked })}
        {field("starts_at", "Starts (required)", { type: "datetime-local", required: !locked })}
        {field("ends_at", "Ends (optional)", { type: "datetime-local" })}
        {field("location", "Location")}
        {field("category", "Category", { list: `${id}-cats`, autoComplete: "off" })}
        <datalist id={`${id}-cats`}>
          {categories.map((c) => (
            <option key={c} value={c} />
          ))}
        </datalist>
        <div className="field wide">
          <label htmlFor={`${id}-notes`}>Notes</label>
          <textarea id={`${id}-notes`} rows={3} value={f.notes} onChange={set("notes")} />
        </div>
      </div>
      {span && (
        <>
          <label className="check">
            <input type="checkbox" checked={canSplit && split} disabled={!canSplit || busy || createdId !== null} onChange={(e) => setSplit(e.target.checked)} />
            Take attendance each day — makes one register per day ({span.days} days)
          </label>
          {span.tooLong ? (
            <p className="muted">Days can only be split for events of up to {MAX_SPLIT_DAYS} days.</p>
          ) : (
            !span.timesOk && <p className="muted">Each day uses the same start and end times, so the end time can't be earlier than the start time.</p>
          )}
        </>
      )}
      {!event && (
        <fieldset className="groups-pick">
          <legend>Save as</legend>
          <label className="check">
            <input type="radio" name={`${id}-status`} checked={status === "draft"} onChange={() => setStatus("draft")} />
            Draft (still planning)
          </label>
          <label className="check">
            <input type="radio" name={`${id}-status`} checked={status === "scheduled"} onChange={() => setStatus("scheduled")} />
            Scheduled (confirmed)
          </label>
        </fieldset>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="row-actions">
        <button type="submit" className="primary" disabled={busy || createdId !== null}>
          {busy ? "Saving…" : event ? "Save changes" : status === "draft" ? "Save as draft" : "Save as scheduled"}
        </button>
        {createdId !== null && <a href={`#events?id=${createdId}`}>Open the event</a>}
        <button type="button" onClick={onCancel}>
          {createdId !== null ? "Close" : "Cancel"}
        </button>
      </div>
    </form>
  );
}
