import { useEffect, useId, useMemo, useRef, useState } from "react";
import { api, isAbort, query } from "../api.js";
import { errorText, formatEventWhen, plural } from "../format.js";

const TEMPLATES = [
  {
    id: "reminder",
    label: "Reminder",
    text: "Reminder: {event} is on {when} at {location}. Please let me know if you can't make it.",
  },
  { id: "change", label: "Change of plan", text: "Update about {event} on {when}: " },
  { id: "thanks", label: "Thank you", text: "Thank you to everyone who came to {event}." },
  { id: "blank", label: "Blank", text: "" },
];

const PLACEHOLDER = /\{(event|when|location)\}/g;

function selectNode(el) {
  if (!el) return;
  const range = document.createRange();
  range.selectNodeContents(el);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
}

export default function Announcements() {
  const id = useId();
  const previewRef = useRef(null);
  const namesRef = useRef(null);
  const emailsRef = useRef(null);

  const [events, setEvents] = useState([]);
  const [groups, setGroups] = useState([]);
  const [loadError, setLoadError] = useState("");
  const [eventId, setEventId] = useState("");
  const [eventDetail, setEventDetail] = useState(null);
  const [template, setTemplate] = useState("reminder");
  const [text, setText] = useState(TEMPLATES[0].text);
  const [audience, setAudience] = useState("all"); // "all" | "event" | a group id
  const [people, setPeople] = useState(null);
  const [peopleError, setPeopleError] = useState("");
  const [copyMsg, setCopyMsg] = useState({ where: "", text: "" }); // where: "message" | "people"
  const [showEmails, setShowEmails] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api("/events?status=scheduled"), api("/groups")])
      .then(([evs, grs]) => {
        if (cancelled) return;
        setEvents([...evs].sort((a, b) => String(a.starts_at).localeCompare(String(b.starts_at))));
        setGroups(grs);
      })
      .catch((e) => !cancelled && setLoadError(errorText(e)));
    return () => {
      cancelled = true;
    };
  }, []);

  // Full details of the chosen event (its expected list, place and time).
  useEffect(() => {
    setEventDetail(null);
    if (!eventId) return undefined;
    let cancelled = false;
    api(`/events/${encodeURIComponent(eventId)}`)
      .then((ev) => !cancelled && setEventDetail(ev))
      .catch((e) => !cancelled && setLoadError(errorText(e)));
    return () => {
      cancelled = true;
    };
  }, [eventId]);

  // "event" only makes sense while an event is chosen.
  const effectiveAudience = audience === "event" && !eventId ? "all" : audience;

  useEffect(() => {
    if (effectiveAudience === "event") {
      setPeopleError("");
      setPeople(eventDetail ? eventDetail.attendees.map((a) => ({ id: a.member_id, full_name: a.full_name, email: a.email })) : null);
      return undefined;
    }
    const ctl = new AbortController();
    setPeople(null);
    api(`/members${query({ active: "true", group_id: effectiveAudience === "all" ? "" : effectiveAudience })}`, { signal: ctl.signal })
      .then((list) => {
        setPeople(list);
        setPeopleError("");
      })
      .catch((e) => !isAbort(e) && setPeopleError(errorText(e)));
    return () => ctl.abort();
  }, [effectiveAudience, eventDetail]);

  const filled = useMemo(() => {
    const ev = eventDetail;
    const values = ev ? { event: ev.name, when: formatEventWhen(ev.starts_at, ev.ends_at), location: ev.location } : {};
    return text.replace(PLACEHOLDER, (whole, key) => values[key] || whole);
  }, [text, eventDetail]);
  const unfilled = [...new Set(filled.match(PLACEHOLDER) ?? [])];

  const names = (people ?? []).map((p) => p.full_name);
  const emails = (people ?? []).map((p) => (p.email || "").trim()).filter(Boolean);
  const noEmail = (people ?? []).length - emails.length;

  const pickTemplate = (value) => {
    setTemplate(value);
    setText(TEMPLATES.find((t) => t.id === value).text);
    setCopyMsg({ where: "", text: "" });
  };

  const copy = async (value, el, where, isEmails = false) => {
    setCopyMsg({ where, text: "" });
    try {
      await navigator.clipboard.writeText(value);
      setCopyMsg({ where, text: "Copied." });
    } catch {
      if (isEmails) setShowEmails(true);
      // wait a tick so the fallback text is on screen before selecting it
      setTimeout(() => selectNode(isEmails ? emailsRef.current : el), 0);
      setCopyMsg({ where, text: "Press Ctrl+C (or Cmd+C) to copy." });
    }
  };

  return (
    <>
      <h1>Announcements</h1>
      <p className="muted">Copy the message and paste it into your Messenger group. The app doesn't send anything.</p>
      {loadError && (
        <p role="alert" className="error">
          {loadError}
        </p>
      )}

      <section className="card" aria-labelledby={`${id}-msg`}>
        <h2 id={`${id}-msg`}>1. Write the message</h2>
        <div className="form-grid">
          <div className="field">
            <label htmlFor={`${id}-event`}>Event (optional)</label>
            <select id={`${id}-event`} value={eventId} onChange={(e) => setEventId(e.target.value)}>
              <option value="">No event</option>
              {events.map((ev) => (
                <option key={ev.id} value={ev.id}>
                  {ev.name} ({formatEventWhen(ev.starts_at, null)})
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor={`${id}-tpl`}>Starting point</label>
            <select id={`${id}-tpl`} value={template} onChange={(e) => pickTemplate(e.target.value)}>
              {TEMPLATES.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="field">
          <label htmlFor={`${id}-text`}>Message</label>
          <textarea id={`${id}-text`} rows={4} value={text} onChange={(e) => setText(e.target.value)} />
          <span className="muted">
            You can type anything. {"{event}"}, {"{when}"} and {"{location}"} are filled in from the chosen event.
          </span>
        </div>

        <h3 id={`${id}-prev`}>Preview</h3>
        {unfilled.length > 0 && (
          <p role="status" className="warning">
            {eventId
              ? `Not filled in: ${unfilled.join(", ")}. The event doesn't have that detail, so edit the message.`
              : `Not filled in: ${unfilled.join(", ")}. Choose an event, or replace it by hand.`}
          </p>
        )}
        <div ref={previewRef} className="preview" tabIndex={-1} aria-labelledby={`${id}-prev`}>
          {filled || <span className="muted">Nothing to show yet.</span>}
        </div>
        <div className="toolbar">
          <button type="button" className="primary" disabled={!filled} onClick={() => copy(filled, previewRef.current, "message")}>
            Copy message
          </button>
          <span role="status" className="status-copy">
            {copyMsg.where === "message" ? copyMsg.text : ""}
          </span>
        </div>
      </section>

      <section className="card" aria-labelledby={`${id}-who`}>
        <h2 id={`${id}-who`}>2. Who is this for?</h2>
        <div className="field">
          <label htmlFor={`${id}-aud`}>Send to</label>
          <select id={`${id}-aud`} value={effectiveAudience} onChange={(e) => setAudience(e.target.value)}>
            <option value="all">Everyone active</option>
            {groups.map((g) => (
              <option key={g.id} value={String(g.id)}>
                Group: {g.name}
              </option>
            ))}
            <option value="event" disabled={!eventId}>
              {eventId ? "Expected at the chosen event" : "Expected at the chosen event (choose an event first)"}
            </option>
          </select>
        </div>
        {peopleError && (
          <p role="alert" className="error">
            {peopleError}
          </p>
        )}
        {!people && !peopleError && <p className="muted">Loading…</p>}
        {people && (
          <>
            <p role="status">
              <strong>{plural(people.length, "person", "people")}</strong>
              {people.length > 0 && noEmail > 0 && <span className="muted"> · {noEmail} without an email address</span>}
            </p>
            {people.length === 0 ? (
              <p className="empty">Nobody to show for this choice.</p>
            ) : (
              <p ref={namesRef} className="preview" tabIndex={-1} aria-label="Names">
                {names.join(", ")}
              </p>
            )}
            {showEmails && (
              <p ref={emailsRef} className="preview" tabIndex={-1} aria-label="Email addresses">
                {emails.join(", ")}
              </p>
            )}
            <div className="toolbar">
              <button type="button" disabled={names.length === 0} onClick={() => copy(names.join(", "), namesRef.current, "people")}>
                Copy names
              </button>
              <button type="button" disabled={emails.length === 0} onClick={() => copy(emails.join(", "), null, "people", true)}>
                Copy emails
              </button>
              <span role="status" className="status-copy">
                {copyMsg.where === "people" ? copyMsg.text : ""}
              </span>
            </div>
          </>
        )}
      </section>
    </>
  );
}
