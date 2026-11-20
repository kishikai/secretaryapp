import { useCallback, useEffect, useId, useState } from "react";
import { api, isAbort, query } from "../api.js";
import { chatLabel, errorText, formatConfidence, formatEventWhen, formatTime, sourceLabel } from "../format.js";

const FILTERS = [
  { id: "pending", label: "Waiting for review", empty: "Nothing waiting for review." },
  { id: "approved", label: "Approved", empty: "Nothing has been approved yet." },
  { id: "rejected", label: "Rejected", empty: "Nothing has been rejected yet." },
];
const STATUSES = [
  { id: "present", label: "Present" },
  { id: "late", label: "Late" },
  { id: "absent", label: "Absent" },
  { id: "excused", label: "Excused" },
];
const STATUS_LABELS = { not_recorded: "Not recorded", ...Object.fromEntries(STATUSES.map((s) => [s.id, s.label])) };
const statusLabel = (s) => STATUS_LABELS[s] ?? "Not recorded";
const eventLink = (id) => `#events?id=${id}`;

function MessageBlock({ item }) {
  const conf = formatConfidence(item.confidence);
  return (
    <>
      <p className="text">{item.message_text}</p>
      <div className="meta">
        <span>{item.sender ? `From ${item.sender}` : "Unknown sender"}</span>
        <span>Chat: {chatLabel(item.chat_id)}</span>
        <span className="chip">{sourceLabel(item.source)}</span>
        {item.source === "ocr" && conf && <span>OCR confidence {conf}</span>}
        <span>Received {formatTime(item.received_at)}</span>
      </div>
    </>
  );
}

function Suggestion({ s }) {
  const none = <span className="muted">No match — choose below</span>;
  return (
    <div className="suggestion">
      <h3>Suggestion</h3>
      <dl className="facts">
        <dt>Who</dt>
        <dd>{s.member_name || none}</dd>
        <dt>Event</dt>
        <dd>
          {s.event_name ? (
            <>
              {s.event_name}
              {s.event_starts_at && <span className="muted"> · {formatEventWhen(s.event_starts_at)}</span>}
            </>
          ) : (
            none
          )}
        </dd>
        <dt>Status</dt>
        <dd>{s.status ? statusLabel(s.status) : none}</dd>
      </dl>
      {s.why?.length > 0 && (
        <ul className="why meta">
          {s.why.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Decision({ item, events }) {
  const r = item.review;
  if (!r) return null;
  const known = events.find((e) => e.id === r.event_id);
  const approved = item.state === "approved";
  return (
    <div className="suggestion">
      <h3>{approved ? "Approved" : "Rejected"}</h3>
      <dl className="facts">
        <dt>By</dt>
        <dd>
          {r.reviewed_by}
          <span className="muted"> · {formatTime(r.reviewed_at)}</span>
        </dd>
        {r.member_name && (
          <>
            <dt>Person</dt>
            <dd>{r.member_name}</dd>
          </>
        )}
        {r.event_id && (
          <>
            <dt>Event</dt>
            <dd>
              <a href={eventLink(r.event_id)}>{r.event_name ?? "View event"}</a>
              {known && <span className="muted"> · {formatEventWhen(known.starts_at)}</span>}
            </dd>
          </>
        )}
        {r.status && (
          <>
            <dt>Status set</dt>
            <dd>{statusLabel(r.status)}</dd>
          </>
        )}
        <dt>Note</dt>
        <dd className="text">{r.note || <span className="muted">None</span>}</dd>
      </dl>
    </div>
  );
}

function ReviewForm({ item, events, version, onDone }) {
  const uid = useId();
  const s = item.suggestion;
  const [eventId, setEventId] = useState(s.event_id ? String(s.event_id) : "");
  const [memberId, setMemberId] = useState(s.member_id ? String(s.member_id) : "");
  const [status, setStatus] = useState(STATUSES.some((x) => x.id === s.status) ? s.status : "");
  const [note, setNote] = useState("");
  const [loaded, setLoaded] = useState(null); // { id, event } for the event the register was last read for
  const [loadError, setLoadError] = useState("");
  const [step, setStep] = useState(null); // "approve" | "reject" | null
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Read the chosen event (expected list and current statuses). Re-read after every review so other cards stay current.
  useEffect(() => {
    if (!eventId) return undefined;
    const ctl = new AbortController();
    setLoadError("");
    api(`/events/${encodeURIComponent(eventId)}`, { signal: ctl.signal })
      .then((event) => setLoaded({ id: eventId, event }))
      .catch((e) => !isAbort(e) && setLoadError(errorText(e)));
    return () => ctl.abort();
  }, [eventId, version]);

  const event = loaded && loaded.id === eventId ? loaded.event : null;
  const people = (event?.attendees ?? []).filter((a) => a.active !== false);
  const person = people.find((a) => String(a.member_id) === memberId);
  const finalised = Boolean(event?.finalized_at);

  // Drop a chosen person who is not on the newly chosen event's list.
  useEffect(() => {
    if (event && memberId && !people.some((a) => String(a.member_id) === memberId)) setMemberId("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [event]);

  const eventOptions = [...events];
  if (s.event_id && !events.some((e) => e.id === s.event_id)) {
    eventOptions.push({ id: s.event_id, name: s.event_name ?? `Event ${s.event_id}`, starts_at: s.event_starts_at });
  }
  const chosenEvent = eventOptions.find((e) => String(e.id) === eventId);
  const complete = Boolean(eventId && memberId && status && person);

  const act = async (path, body, doneText) => {
    setBusy(true);
    setError("");
    try {
      await api(`/evidence/${item.message_id}/${path}`, { method: "POST", body });
      await onDone(doneText);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setStep(null);
      setBusy(false);
    }
  };
  const trimmed = note.trim();
  const approve = () => {
    const body = { member_id: Number(memberId), event_id: Number(eventId), status };
    if (trimmed) body.note = trimmed;
    return act("approve", body, `${person.full_name} set to ${statusLabel(status)} for ${chosenEvent?.name ?? "the event"}.`);
  };
  const reject = () => act("reject", trimmed ? { note: trimmed } : {}, "Message rejected. The register was not changed.");

  const approveText = person
    ? `Set ${person.full_name} to ${statusLabel(status)} for ${chosenEvent?.name ?? "this event"}?`
    : "";

  return (
    <form
      className="review-form"
      aria-label={`Review message ${item.message_id}`}
      onSubmit={(e) => {
        e.preventDefault();
        if (complete && !finalised) setStep("approve");
      }}
    >
      <div className="field">
        <label htmlFor={`${uid}-event`}>Event</label>
        <select
          id={`${uid}-event`}
          value={eventId}
          disabled={busy}
          onChange={(e) => {
            setEventId(e.target.value);
            setStep(null);
          }}
        >
          <option value="">Choose an event…</option>
          {eventOptions.map((e) => (
            <option key={e.id} value={e.id}>
              {e.name} · {formatEventWhen(e.starts_at)}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor={`${uid}-member`}>Person</label>
        <select
          id={`${uid}-member`}
          value={memberId}
          disabled={busy || !event}
          onChange={(e) => {
            setMemberId(e.target.value);
            setStep(null);
          }}
        >
          <option value="">{!eventId ? "Choose an event first" : event ? "Choose a person…" : "Loading…"}</option>
          {people.map((a) => (
            <option key={a.member_id} value={a.member_id}>
              {a.full_name}
            </option>
          ))}
        </select>
        {event && people.length === 0 && <span className="muted">No one is on this event's expected list.</span>}
        {person && <span className="muted">Currently: {statusLabel(person.status)}</span>}
      </div>
      <div className="field">
        <label htmlFor={`${uid}-status`}>Status</label>
        <select
          id={`${uid}-status`}
          value={status}
          disabled={busy}
          onChange={(e) => {
            setStatus(e.target.value);
            setStep(null);
          }}
        >
          <option value="">Choose a status…</option>
          {STATUSES.map((x) => (
            <option key={x.id} value={x.id}>
              {x.label}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor={`${uid}-note`}>Note (optional, kept in the change history)</label>
        <input id={`${uid}-note`} type="text" maxLength={500} value={note} disabled={busy} onChange={(e) => setNote(e.target.value)} />
      </div>

      {loadError && (
        <p role="alert" className="error">
          {loadError}
        </p>
      )}
      {finalised && (
        <p role="status" className="banner">
          This register is finalised. Reopen it from the event page first. <a href={eventLink(eventId)}>Open the event</a>
        </p>
      )}

      {step === null && (
        <div className="row-actions">
          <button type="submit" className="primary" disabled={busy || !complete || finalised}>
            Approve and update register
          </button>
          <button type="button" disabled={busy} onClick={() => setStep("reject")}>
            Reject
          </button>
        </div>
      )}
      {step === "approve" && (
        <>
          <p role="status">{approveText}</p>
          <div className="row-actions">
            <span className="confirm">
              <button type="button" className="primary" disabled={busy} onClick={approve}>
                {busy ? "Saving…" : "Yes, update register"}
              </button>
              <button type="button" disabled={busy} onClick={() => setStep(null)}>
                Keep editing
              </button>
            </span>
          </div>
        </>
      )}
      {step === "reject" && (
        <>
          <p role="status">Reject this message? The register will not change.</p>
          <div className="row-actions">
            <span className="confirm">
              <button type="button" className="danger" disabled={busy} onClick={reject}>
                {busy ? "Saving…" : "Yes, reject"}
              </button>
              <button type="button" disabled={busy} onClick={() => setStep(null)}>
                Keep editing
              </button>
            </span>
          </div>
        </>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </form>
  );
}

export default function Evidence() {
  const [state, setState] = useState("pending");
  const [items, setItems] = useState([]);
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [version, setVersion] = useState(0);

  // Candidate events: scheduled and completed, newest first.
  useEffect(() => {
    const ctl = new AbortController();
    Promise.all(["scheduled", "completed"].map((st) => api(`/events${query({ status: st })}`, { signal: ctl.signal })))
      .then(([a, b]) => {
        const byId = new Map([...a, ...b].map((e) => [e.id, e]));
        setEvents([...byId.values()].sort((x, y) => String(y.starts_at ?? "").localeCompare(String(x.starts_at ?? ""))));
      })
      .catch((e) => !isAbort(e) && setError(errorText(e)));
    return () => ctl.abort();
  }, []);

  useEffect(() => {
    const ctl = new AbortController();
    api(`/evidence${query({ state })}`, { signal: ctl.signal })
      .then((rows) => {
        setItems(rows);
        setError("");
        setLoading(false);
      })
      .catch((e) => {
        if (isAbort(e)) return;
        setItems([]);
        setError(errorText(e));
        setLoading(false);
      });
    return () => ctl.abort();
  }, [state, version]);

  const choose = (id) => {
    if (id === state) return;
    setState(id);
    setItems([]);
    setLoading(true);
    setError("");
    setNotice("");
  };

  const done = useCallback(async (text) => {
    setNotice(text);
    setVersion((v) => v + 1);
  }, []);

  const current = FILTERS.find((f) => f.id === state);

  return (
    <>
      <h1>Review</h1>
      <p className="notice">Messages are only evidence. Nothing changes a register until you approve it, and a missing message never means someone was absent.</p>
      <div className="toolbar">
        <div className="segmented" role="group" aria-label="Show">
          {FILTERS.map((f) => (
            <button key={f.id} type="button" aria-pressed={state === f.id} onClick={() => choose(f.id)}>
              {f.label}
            </button>
          ))}
        </div>
      </div>
      <div role="status" className="status-msg">
        {notice && <span className="notice">{notice}</span>}
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {loading && <p className="muted">Loading…</p>}
      {!loading && !error && items.length === 0 && <p className="empty">{current.empty}</p>}

      <ul className="list">
        {items.map((item) => (
          <li key={item.message_id} className="card item evidence-item">
            <MessageBlock item={item} />
            {item.state === "pending" ? (
              <>
                <Suggestion s={item.suggestion} />
                <ReviewForm item={item} events={events} version={version} onDone={done} />
              </>
            ) : (
              <Decision item={item} events={events} />
            )}
          </li>
        ))}
      </ul>
    </>
  );
}
